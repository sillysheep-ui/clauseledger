"""重排层的纯函数部分：不加载模型，只测输入构造与长度分桶对齐。"""
from __future__ import annotations

from app.retrieval import rerank


def test_max_len_follows_model_and_env(monkeypatch):
    assert rerank.max_len("BAAI/bge-reranker-v2-m3") == 8192
    assert rerank.max_len("BAAI/bge-reranker-base") == 512
    monkeypatch.setenv("CLAUSELEDGER_RERANK_MAXLEN", "2048")
    assert rerank.max_len("BAAI/bge-reranker-v2-m3") == 2048


class _FakeModel:
    """长度越长分越低：用来验证长度分桶后仍按原顺序返回分数。"""

    def predict(self, pairs, batch_size=8, show_progress_bar=False):
        return [-float(len(t)) for _, t in pairs]


def test_predict_keeps_input_order_when_bucketed():
    pairs = [("q", "x" * n) for n in (5, 300, 20, 120, 8)]
    scores = rerank._predict(_FakeModel(), pairs, batch_size=2)
    assert scores == [-5.0, -300.0, -20.0, -120.0, -8.0]
