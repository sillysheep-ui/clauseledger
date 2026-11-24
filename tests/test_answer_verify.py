import pytest

from app.answer.context import Evidence
from app.answer.generate import Draft
from app.answer.verify import check


def ev():
    return [Evidence("E1", "c1", "沪人社规〔2026〕1号", "某通知", ["一、"], 3,
                     "失业保险金标准为 2340 元/月，最长不超过 24 个月。", "hit", "《沪人社规〔2026〕1号》一、（第 3 页）"),
            Evidence("E2", "c2", "沪人社规〔2026〕2号", "另一通知", ["二、"], 7,
                     "申领需提供身份证。", "hit", "《沪人社规〔2026〕2号》二、（第 7 页）")]


def test_valid_sentence_is_kept():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。", "cites": ["E1"]}])
    r = check(d, ev())
    assert len(r.kept) == 1 and r.ok


def test_missing_cite_is_dropped():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。"}])
    r = check(d, ev())
    assert not r.kept and "没有引用" in r.dropped[0]["why"]


def test_unknown_cite_is_dropped():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。", "cites": ["E9"]}])
    r = check(d, ev())
    assert not r.kept and "不存在" in r.issues[0]


def test_number_not_in_evidence_is_dropped():
    d = Draft(sentences=[{"text": "标准为 9999 元/月。", "cites": ["E1"]}])
    r = check(d, ev())
    assert not r.kept and "9999" in r.issues[0]


def test_rules_numbers_are_allowed():
    d = Draft(sentences=[{"text": "按规则算得 71 分。", "cites": ["E1"]}])
    r = check(d, ev(), rules_text="已知 71 分 / 标准 72 分")
    assert r.kept, r.issues


def test_assemble_expands_within_anchor_docs(monkeypatch):
    """新装配：锚点文件内多条都进上下文，而不是只给命中那一条的邻居。"""
    pytest.importorskip("bm25s")
    from pathlib import Path as _P

    if not _P("data/index/clauses.jsonl").exists():
        pytest.skip("语料不入库")
    from app.answer import context

    # 锚点策略现在是显式开关（默认是纯条款装配）
    monkeypatch.setenv("CLAUSELEDGER_ASSEMBLE", "anchors")
    from app.retrieval.search import search_result

    a = search_result("失业保险金怎么领", k=3, min_answer=0, min_score_strong=0,
                      min_margin_strong=0)
    ev = context.assemble("失业保险金怎么领", a.hits, max_chars=12000)
    docs = [e.doc_no for e in ev]
    assert len(ev) >= 5
    # 至少有一份文件贡献了 2 条以上（同文件展开）
    assert any(docs.count(d) >= 2 for d in set(docs))
    # 每条都有引用串，可回溯
    assert all(e.citation for e in ev)


def test_assemble_legacy_switch(monkeypatch):
    pytest.importorskip("bm25s")
    from pathlib import Path as _P

    if not _P("data/index/clauses.jsonl").exists():
        pytest.skip("语料不入库")
    from app.answer import context
    from app.retrieval.search import search_result

    a = search_result("失业保险金怎么领", k=3, min_answer=0, min_score_strong=0,
                      min_margin_strong=0)
    monkeypatch.setenv("CLAUSELEDGER_ASSEMBLE", "legacy")
    ev = context.assemble("失业保险金怎么领", a.hits)
    assert ev and all(e.role in ("hit", "parent", "neighbor") for e in ev)
