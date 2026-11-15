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
    S.search("任意", k=3, min_score=1.5, min_coverage=0.7, min_cand_cov=0.9)
    assert seen["k"] == 3
    assert seen["min_score"] == 1.5
    assert seen["min_coverage"] == 0.7
    assert seen["min_cand_cov"] == 0.9, "search() 又吞掉一个阈值参数"


def test_unreachable_threshold_actually_refuses():
    """用不可达值当探针：阈值若被忽略，就一定答出来。"""
    q = "失业保险金标准"
    loose = S.search_result(q, k=5, min_coverage=0.0)
    if not loose.hits:
        import pytest
        pytest.skip("索引未构建")
    assert S.search_result(q, k=5, min_coverage=1.01).outcome != "answer"
    assert S.search_result(q, k=5, min_cand_cov=1.01).hits == []
