"""混合召回（BM25 + 稠密，RRF 融合）的单元测试。

这些测试刻意**不加载真模型**：模型下载与 MPS 推理不该是跑 `pytest` 的前提。
所以融合规则用纯函数测，模式切换用 monkeypatch 测。
"""
from __future__ import annotations

import pytest

from app.retrieval import dense
from app.retrieval import search as S


def test_rrf_formula_and_union():
    # 1 号在两路都排第一，必然最高；3 号只在 B 出现，分数 = 1/(60+1)
    fused = dict(dense.rrf([1, 2], [1, 3], k=60))
    assert set(fused) == {1, 2, 3}
    assert fused[1] == pytest.approx(2 / 61)
    assert fused[2] == pytest.approx(1 / 62)
    assert fused[3] == pytest.approx(1 / 62)   # B 里排第 2
    # 2 号（B 里没有）与 3 号（A 里没有）分数相同，但顺序要稳定可复现
    assert dense.rrf([1, 2], [1, 3], k=60) == dense.rrf([1, 2], [1, 3], k=60)


def test_rrf_ignores_raw_scores_only_ranks():
    # RRF 只看名次：两路"分数"再大也不影响融合结果
    a = [10, 11]
    b = [21, 20]
    assert [c for c, _ in dense.rrf(a, b)] == [10, 21, 11, 20]


def test_recall_mode_respects_bm25_override(monkeypatch):
    # 默认已切 hybrid；显式设 bm25 时必须真的走 bm25（回退路径要可测）
    monkeypatch.setattr(S, "RECALL", "bm25")
    assert S.recall_mode() == "bm25"


def test_recall_mode_falls_back_when_model_missing(monkeypatch):
    monkeypatch.setattr(S, "RECALL", "hybrid")
    monkeypatch.setattr(dense, "available", lambda: False)
    assert S.recall_mode() == "bm25"


def test_recall_mode_keeps_hybrid_when_available(monkeypatch):
    monkeypatch.setattr(S, "RECALL", "hybrid")
    monkeypatch.setattr(dense, "available", lambda: True)
    assert S.recall_mode() == "hybrid"


def test_searched_records_actual_recall_mode():
    # 审计要求：实际用了哪路召回必须写进检索痕迹，不能默认填 bm25
    ans = S.search_result("北京落户需要几年社保", k=2)
    assert ans.kind == "not_covered"
    assert ans.searched["recall"] == "none"
    assert ans.searched["n_queries"] == 0
