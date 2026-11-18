"""三张卡的守则测试：逐字引用、三种分派、拒答必须留痕。"""
from __future__ import annotations

import re

from app.answer import cards


def _hit(text="硕士、研究生 24 分。", doc="沪教委学〔2026〕13号", page=21):
    return {"doc_no": doc, "title": "关于做好 2026 年非上海生源应届普通高校毕业生进沪就业工作的通知",
            "citation": f"《{doc}》一、 / （一） / 1.（第 {page} 页）", "page": page,
            "path": ["一、", "（一）", "1."], "quote": "", "text": text,
            "score": 9.2, "coverage": 0.5, "stale": "", "scope": ""}


class FakeState:
    def __init__(self, **kw):
        self.answer = kw.get("answer") or {}
        self.judgement = kw.get("judgement") or {}
        self.hits = kw.get("hits") or []
        self.year = kw.get("year")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def test_quote_never_breaks_a_sentence():
    text = "第一条 为了规范管理，制定本办法。" * 5
    q = cards.quote(text, 40)
    assert q and q in text
    assert q.endswith(("。", "；", "！", "？")) or len(text) <= 40


def test_verdict_card_carries_score_gap_and_pages():
    # 真实情形：硕士拉满基本要素 = 71 分，标准 72，至少差 1
    st = FakeState(
        judgement={"verdict": "unknown", "total_known": 71, "total_upper": 89,
                   "standard": 72, "source": "luohu-2026",
                   "items": [{"label": "最高学位、学历 / 硕士", "state": "satisfied",
                              "points": 24, "page": 21},
                             {"label": "外语水平", "state": "unknown", "points": None,
                              "page": 22}]},
        hits=[_hit()])
    card = cards.build(st)
    assert card["kind"] == "verdict"
    assert card["score"] == {"known": 71, "upper": 89, "standard": 72, "gap": 1}
    assert card["basis"][0]["page"] == 21
    assert card["missing"] == ["外语水平"]
    assert "还差 1 分" in card["headline"]


def test_unsatisfied_only_when_even_the_upper_bound_fails():
    st = FakeState(
        judgement={"verdict": "unsatisfied", "total_known": 50, "total_upper": 68,
                   "standard": 72, "source": "luohu-2026",
                   "items": [{"label": "最高学位、学历 / 本科", "state": "satisfied",
                              "points": 21, "page": 21}]},
        hits=[_hit()])
    card = cards.build(st)
    assert card["kind"] == "verdict"
    assert card["score"]["gap"] == 4          # 68 的上限仍低于 72
    assert "68" in card["headline"] and "72" in card["headline"]


def test_unknown_without_a_shortfall_does_not_invent_one():
    st = FakeState(
        judgement={"verdict": "unknown", "total_known": 80, "total_upper": 95,
                   "standard": 72, "source": "luohu-2026",
                   "items": [{"label": "荣誉", "state": "unknown", "page": 24}]},
        hits=[_hit()])
    card = cards.build(st)
    assert card["kind"] == "verdict"
    assert card["score"]["gap"] is None    # 已知分够，不能编"还差"
    assert "还差" not in card["headline"]
    assert card["missing"] == ["荣誉"]


def test_no_judgement_falls_back_to_standard_card_with_verbatim_quotes():
    st = FakeState(answer={"kind": "ok", "as_of": "2026-12-31"},
                   judgement={"verdict": "unknown", "source": "none"},
                   hits=[_hit("失业保险金标准为第 1–12 个月 2255 元/月。")])
    card = cards.build(st)
    assert card["kind"] == "standard"
    item = card["items"][0]
    assert item["quote"] == "失业保险金标准为第 1–12 个月 2255 元/月。"
    assert item["citation"].endswith("（第 21 页）")
    assert card["depends_on"]          # 必须告诉用户"还缺什么"


def test_refusal_card_keeps_search_evidence():
    st = FakeState(answer={"kind": "insufficient", "reason": "没有找到直接对应的条款",
                           "searched": {"n_docs": 10, "n_queries": 11}},
                   hits=[_hit()])
    card = cards.build(st)
    assert card["kind"] == "refusal"
    assert card["searched"] == {"n_docs": 10, "n_queries": 11}
    assert card["evidence"] and card["evidence"][0]["citation"]


def test_not_covered_never_claims_it_searched():
    st = FakeState(answer={"kind": "not_covered", "refusal": "未接入北京",
                           "searched": {"n_docs": 0, "n_queries": 0}})
    card = cards.build(st)
    assert card["kind"] == "refusal"
    assert card["searched"]["n_queries"] == 0
    assert card["evidence"] == []
    assert "没有接入" in card["headline"]


def test_evidence_quote_is_substring_of_cited_clause():
    text = "一、申请条件。在本市依法参加失业保险并足额缴费满一年的失业人员。"
    st = FakeState(answer={"kind": "ok"}, hits=[_hit(text)])
    card = cards.build(st)
    q = card["evidence"][0]["quote"]
    assert _norm(q) and _norm(q) in _norm(text)
