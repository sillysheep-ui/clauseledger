"""阈值参数必须真的传到判定处——这个 bug 犯过两次。

2026-11-15 第一次：`search_result` 的 `min_coverage` 只用于候选过滤，
最后的拒答判定写死模块常量 `MIN_COVERAGE`，传参等于没传。
第二次：`search()` 收下了 `min_cand_cov` 却没往下传。
两次都不会报错，只会让"调参数"的实验得出反向结论。
"""
from app.retrieval import search as S


def test_search_forwards_all_thresholds(monkeypatch):
    seen = {}

    def fake(query, **kw):
        seen.update(kw)
        return S.Answer([], "", "ok", 0, 0, searched={"n_docs": 0, "n_queries": 0})

    monkeypatch.setattr(S, "search_result", fake)
    S.search("任意", k=3, min_score=1.5, min_coverage=0.7, min_cand_cov=0.9,
             min_answer=0.2, min_score_strong=5.5, min_margin_strong=2.5)
    assert seen["k"] == 3
    assert seen["min_score"] == 1.5
    assert seen["min_coverage"] == 0.7
    assert seen["min_cand_cov"] == 0.9, "search() 又吞掉一个阈值参数"
    assert seen["min_answer"] == 0.2, "search() 吞掉了 min_answer"
    assert seen["min_score_strong"] == 5.5, "search() 吞掉了 min_score_strong"
    assert seen["min_margin_strong"] == 2.5, "search() 吞掉了 min_margin_strong"


def test_unreachable_threshold_actually_refuses():
    """用不可达值当探针：阈值若被忽略，就一定答出来。"""
    q = "失业保险金标准"
    loose = S.search_result(q, k=5, min_coverage=0.0)
    if not loose.hits:
        import pytest
        pytest.skip("索引未构建")
    # 2026-11-17 起阈值分两档：min_answer 决定"拒不拒"，min_coverage 决定"标不标低置信"
    assert S.search_result(q, k=5, min_answer=1.01).outcome != "answer"
    assert S.search_result(q, k=5, min_cand_cov=1.01).hits == []
    # 0.5 这条覆盖率线已经不再决定"拒不拒"，只用于展示；敢不敢下结论看分数
    assert S.search_result(q, k=5, min_score_strong=999, min_margin_strong=999).confidence == "low"


def test_no_direct_clause_keeps_candidates_but_does_not_claim_answer():
    """分数不硬时不许说"就是这条"——但仍要把候选交出来，不能假装没查到。"""
    q = "失业保险金标准"
    a = S.search_result(q, k=5, min_score_strong=999.0, min_margin_strong=999.0)
    if not a.hits:
        import pytest
        pytest.skip("索引未构建")
    assert a.kind == "no_direct_clause"
    assert a.outcome == "no_direct_clause"
    assert a.confidence == "low"
    assert a.hits, "no_direct_clause 必须保留候选"

    b = S.search_result(q, k=5, min_score_strong=0.0, min_margin_strong=0.0, min_coverage=0.0, min_answer=0.0)
    assert b.kind == "ok", "分数门槛放到 0 就该正常作答"


def test_two_path_agreement_is_recorded(monkeypatch):
    """两路召回是否指向同一份文件，必须写进 searched —— 它是"敢不敢答"的依据之一。"""
    from app.retrieval import search as S

    ret, clauses, children = S._load()
    best = S._to_pids([(0, 9.0)], children)
    monkeypatch.setattr(S, "_candidates",
                        lambda *a, **k: (best, "hybrid", {"bm25": [0], "dense": [0]}))
    ans = S.search_result("失业保险金怎么领", k=3, min_coverage=0.0, min_cand_cov=0.0,
                          min_score=0.0, min_score_strong=0.0)
    assert ans.searched["agree"] is True


def test_two_path_disagreement_is_recorded(monkeypatch):
    from app.retrieval import search as S

    ret, clauses, children = S._load()
    best = S._to_pids([(0, 9.0)], children)
    other = next(i for i, c in enumerate(clauses) if c.doc_no != clauses[0].doc_no)
    monkeypatch.setattr(S, "_candidates",
                        lambda *a, **k: (best, "hybrid", {"bm25": [0], "dense": [other]}))
    ans = S.search_result("失业保险金怎么领", k=3, min_coverage=0.0, min_cand_cov=0.0,
                          min_score=0.0, min_score_strong=0.0)
    assert ans.searched["agree"] is False
