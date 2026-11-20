"""trace 的两条性质：**一个 run 只有一棵树**，且跨进程恢复仍挂回这棵树。"""
from __future__ import annotations

import json

import pytest

from app.runtime import Engine, Store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_TRACE", str(tmp_path / "traces.jsonl"))
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    return tmp_path / "traces.jsonl"


def _spans(p):
    return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]


def test_one_run_one_trace(env):
    st = Engine(Store()).run("失业保险金怎么领")
    rows = _spans(env)
    assert rows, "应该有 span"
    assert {r["trace_id"] for r in rows} == {st.trace_id}
    assert len({r["name"] for r in rows}) >= 3


def test_resume_keeps_the_same_trace(env):
    PROFILE = {"degree": "master", "school_tier": 1, "school_in_shanghai": True,
               "gpa_rank": "a", "foreign": "cet6_or_tem8", "computer": "grad",
               "employer_key_field": True, "honor_national": True}
    eng = Engine(Store())
    s = eng.run("硕士毕业落户能加多少分", PROFILE)
    assert s.gate and s.gate["kind"] == "action"
    before = {r["trace_id"] for r in _spans(env)}

    eng.resume(s.run_id, {"action": "approve", "by": "t"})
    after = {r["trace_id"] for r in _spans(env)}
    assert after == before == {s.trace_id}          # 恢复没有开新树


def test_spans_are_nested(env):
    Engine(Store()).run("失业保险金怎么领")
    rows = _spans(env)
    parents = {r["span_id"]: r.get("parent_id") for r in rows}
    assert any(p for p in parents.values() if p), "至少有一个 span 带父节点"
