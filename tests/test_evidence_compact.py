"""证据"少而整"：按条数与总字数控制，截断必须显式，绝不静默。"""
from __future__ import annotations

from app.tools import service as T


def test_compact_is_small_and_complete():
    d = T.evidence_compact("失业保险金怎么领", max_items=8, budget=2600)
    assert d["refused"] is False
    assert 1 <= d["n"] <= 8
    assert d["budget_used"] <= 2600
    # 没有截断的条目，text 就是完整条款；截断的必须带标记和字数
    for e in d["evidence"]:
        if e["truncated"]:
            assert len(e["text"]) == 1200 and e["chars"] > 1200
        else:
            assert len(e["text"]) == e["chars"]


def test_compact_reports_how_many_were_left_out():
    full = T.evidence("失业保险金怎么领")
    d = T.evidence_compact("失业保险金怎么领", max_items=3, budget=2000)
    assert d["n"] <= 3
    assert d["n_available"] == len(full["evidence"])
    assert d["n_available"] >= d["n"]


def test_compact_keeps_refusal_reason():
    d = T.evidence_compact("北京落户需要几年社保")
    assert d["refused"] is True
    assert d["kind"] == "not_covered"
    assert d["n"] == 0 and d["evidence"] == []
