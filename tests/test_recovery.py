"""崩溃恢复：不是"看起来能续"，而是**副作用只发生一次**。

两个崩溃点分别测：
1. 两个 step 之间（进程在等人批准时被杀）；
2. 一个 step 内部、副作用已经写下去之后（最坏的情况）。
"""
from __future__ import annotations

import pytest

from app.runtime import Engine, Store, actions, steps


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    monkeypatch.setenv("CLAUSELEDGER_DB", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("CLAUSELEDGER_GENERATE", "0")
    return Engine(Store(tmp_path / "runs"))


def test_resume_running_state_after_crash_between_steps(engine, monkeypatch):
    s = engine.start("失业保险金能领多久")
    # 模拟"进程在第一个 step 之前被 kill"：库里留着 status=running / step=normalize
    real = steps.STEPS_FN["normalize"]
    monkeypatch.setitem(steps.STEPS_FN, "normalize",
                        lambda st, d: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        engine.advance(s)
    monkeypatch.setitem(steps.STEPS_FN, "normalize", real)

    got = engine.resume(s.run_id)          # 不加决定，直接重入
    assert got.status in ("waiting", "done")
    kinds = [e["kind"] for e in engine.store.events(s.run_id)]
    assert "run_recovered" in kinds


def test_resume_after_act_written_does_not_duplicate(engine, monkeypatch):
    s = engine.run("博士毕业落户能加多少分", {"degree": "phd"},
                   approvals={"action": {"action": "approve", "by": "t"}})
    # 上面已写 3 条；把它当成"act 写完、step_finished 还没落盘就被 kill"
    before = actions.count()
    assert before == 3

    st = engine.store.load(s.run_id)
    st.status, st.step, st.gate = "running", "act", None
    engine.store.commit(st, "act", "run_recovered", {"step": "act"})

    got = engine.resume(s.run_id)
    assert got.status == "done"
    assert actions.count() == 3, "重入 act 不能再写一遍"
    res = got.action["result"]
    assert res["applied"] == 0 and res["reused"] == 3
