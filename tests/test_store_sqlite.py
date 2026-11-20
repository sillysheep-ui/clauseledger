"""SQLite 后端要证明的是**并发下的两条性质**：

1. 同一个副作用执行两次（哪怕两个线程同时）只落一条；
2. 状态与事件同一个事务——事件写失败时状态不能前进。

这两条在 JSONL 后端上做不到（序号数行数、幂等读全表再写回），
所以这组用例只对默认的 sqlite 后端起约束。
"""
from __future__ import annotations

import threading

import pytest

from app.runtime import actions, db
from app.runtime.state import RunState


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    from app.runtime.store import Store
    return Store()


def _state(rid="run_t1", status="running", step="retrieve"):
    return RunState(run_id=rid, question="q", status=status, step=step)


def test_event_seq_is_per_run_and_monotonic(store):
    a = _state("run_a")
    b = _state("run_b")
    store.save(a)
    store.save(b)
    for i in range(3):
        store.event(a, "retrieve", "x", {"i": i})
        store.event(b, "retrieve", "x", {"i": i})
    assert [e["seq"] for e in store.events("run_a")] == [1, 2, 3]
    assert [e["seq"] for e in store.events("run_b")] == [1, 2, 3]


def test_concurrent_apply_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    items = [{"title": f"待办{i}"} for i in range(5)]
    results = []
    lock = threading.Lock()

    def go():
        r = actions.apply("run_c", "todo", {"items": items})
        with lock:
            results.append(r)

    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert actions.count("todo") == 5                       # 世界只写了 5 条
    assert sum(r["applied"] for r in results) == 5          # 总共只有 5 次"新增"
    assert sum(r["reused"] for r in results) == 15           # 其余 15 次都是命中已有键


def test_commit_is_atomic(store, monkeypatch):
    s = _state("run_atomic", step="judge")

    def boom(*a, **k):
        raise RuntimeError("event 写失败")

    monkeypatch.setattr(db, "append_event", boom)
    with pytest.raises(RuntimeError):
        store.commit(s, "judge", "step_finished", {})
    # 事件失败 → 状态不能前进（否则审计流里会缺一步）
    assert not store.exists("run_atomic")


def test_concurrent_commit_keeps_all_events(store):
    s = _state("run_cc")
    store.save(s)

    def go(i):
        # 每个线程写自己的一份快照，事件必须一条不少
        st = RunState.from_json(store.load("run_cc").to_json())
        st.step = "retrieve"
        store.commit(st, "retrieve", "noise", {"i": i})

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert len(store.events("run_cc")) == 8
