"""运行时骨架的验收：**不是"能跑"，是"能停、能恢复、不重复"**。

对应 docs/FOUNDATION.md §8 的三条：
- 每个 step 有开始/结束事件；
- 恢复只重入被中断的 step；
- 副作用幂等（批准两次只写一次）。
"""
from __future__ import annotations

import json

import pytest

from app.runtime import Engine, Store
from app.runtime import actions
from app.runtime.state import STEPS


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    return Engine(Store(tmp_path / "runs"))


def started(store, rid):
    return [e["step"] for e in store.events(rid) if e["kind"] == "step_started"]


def kinds(store, rid):
    return [e["kind"] for e in store.events(rid)]


PROFILE = {
    "degree": "master", "school_tier": 1, "school_in_shanghai": True, "gpa_rank": "a",
    "foreign": "cet6_or_tem8", "computer": "grad", "employer_key_field": True,
    "honor_national": True,
}


def test_unknown_stops_at_judgement_gate(eng):
    s = eng.run("正高级工程师评审条件是什么")
    assert s.status == "waiting"
    assert s.gate["kind"] == "judgement"
    assert s.judgement["verdict"] == "unknown"
    assert s.action is None                       # 没判定就没有动作
    ev = kinds(eng.store, s.run_id)
    assert ev[-1] == "gate_opened"
    assert started(eng.store, s.run_id)[0] == "normalize"


def test_resume_confirm_unknown_finishes_without_action(eng):
    s = eng.run("正高级工程师评审条件是什么")
    s = eng.resume(s.run_id, {"action": "confirm_unknown", "by": "tester"})
    assert s.status == "done"
    assert s.judgement["confirmed"] is True
    assert actions.count() == 0
    assert "gate_resolved" in kinds(eng.store, s.run_id)


def test_satisfied_gates_action_then_approve_writes_once(eng):
    s = eng.run("硕士毕业落户能加多少分", PROFILE)
    assert s.status == "waiting" and s.gate["kind"] == "action"
    assert s.judgement["verdict"] in ("satisfied", "direct")
    assert actions.count() == 0                   # 未批准前世界不变

    s = eng.resume(s.run_id, {"action": "approve", "by": "tester"})
    assert s.status == "done"
    assert actions.count() == 3
    assert s.action["result"]["applied"] == 3

    # 同一个键再执行一次：不产生第二条
    again = actions.apply(s.run_id, "todo", {"items": s.action["items"]})
    assert again["applied"] == 0 and again["reused"] == 3
    assert actions.count() == 3


def test_deny_leaves_world_untouched(eng):
    s = eng.run("硕士毕业落户能加多少分", PROFILE)
    s = eng.resume(s.run_id, {"action": "deny", "by": "tester"})
    assert s.status == "done"
    assert actions.count() == 0
    assert (s.action or {}).get("denied") is True


def test_resume_does_not_repeat_finished_steps(eng):
    s = eng.run("正高级工程师评审条件是什么")
    before = started(eng.store, s.run_id)
    assert before[-1] == "gate_judgement"

    s = eng.resume(s.run_id, {"action": "confirm_unknown"})
    after = started(eng.store, s.run_id)
    # 恢复后只重入被中断的 gate_judgement，之前的步骤一次都不再出现
    assert after == before + ["gate_judgement", "compose", "verify", "gate_action", "act", "finalize"]
    assert len(s.done_steps) == len(set(s.done_steps))
    assert s.done_steps == [x for x in STEPS if x in s.done_steps]


def test_events_are_append_only_with_sequence(eng):
    s = eng.run("正高级工程师评审条件是什么")
    ev = eng.store.events(s.run_id)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))
    # 走完，再试一次恢复：必须拒绝，且历史一行不改
    s = eng.resume(s.run_id, {"action": "confirm_unknown"})
    assert s.status == "done"
    n = len(eng.store.events(s.run_id))
    with pytest.raises(ValueError):
        eng.resume(s.run_id, {"action": "confirm_unknown"})
    assert eng.store.events(s.run_id)[:n] == eng.store.events(s.run_id)


def test_blocked_city_never_retrieves(eng):
    s = eng.run("北京落户需要几年社保")
    assert s.status in ("done", "waiting")
    assert started(eng.store, s.run_id) == ["normalize", "coverage", "retrieve", "judge",
                                            "gate_judgement", "compose", "verify",
                                            "gate_action", "act", "finalize"]
    assert s.hits == []
    assert s.answer["kind"] == "not_covered"      # 检索层自己的城市闸拒绝，且 n_queries=0
    assert (s.answer.get("searched") or {}).get("n_queries") == 0


def test_state_roundtrip_is_json(eng):
    s = eng.run("正高级工程师评审条件是什么")
    blob = s.to_json()
    back = type(s).from_json(blob)
    assert back.run_id == s.run_id and back.status == s.status
    json.dumps(json.loads(blob))  # 必须能再次序列化


def test_resume_from_a_fresh_process(tmp_path, monkeypatch):
    """模拟"进程重启"：内存里什么都不留，只有磁盘上的 state.json。"""
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    a = Engine(Store(tmp_path / "runs"))
    s = a.run("正高级工程师评审条件是什么")
    assert s.status == "waiting"

    b = Engine(Store(tmp_path / "runs"))          # 新实例 = 新进程
    s2 = b.resume(s.run_id, {"action": "confirm_unknown"})
    assert s2.status == "done"
    assert s2.run_id == s.run_id
    assert b.store.events(s.run_id) == a.store.events(s.run_id)
