"""可选重排层：用 cross-encoder 给 (问题, 条款) 打分，替换 BM25 的排序。

为什么放在 _collapse 之前：条款级才有足够信号。把同一份文件压成一条再重排，
喂给模型的是"这份文件最像问题的那一条"，而它未必是能回答问题的条款——实测
在 collapse 之后重排会把 hit@1 从 58.7% 打到 41.3%。

开关（默认开启，缺模型时自动降级、不报错）：
  CLAUSELEDGER_RERANK=0              关闭
  CLAUSELEDGER_RERANK_MODEL=...      默认 BAAI/bge-reranker-base
  CLAUSELEDGER_RERANK_TOP=40         重排前 N 条条款
"""
from __future__ import annotations

import os

_DEFAULT_MODEL = "BAAI/bge-reranker-base"
_model = None
_model_name: str | None = None


def enabled() -> bool:
    return os.environ.get("CLAUSELEDGER_RERANK", "1") not in ("", "0", "false", "no")


def top_n() -> int:
    try:
        return int(os.environ.get("CLAUSELEDGER_RERANK_TOP", "40"))
    except ValueError:
        return 40


def _load():
    global _model, _model_name
    name = os.environ.get("CLAUSELEDGER_RERANK_MODEL", _DEFAULT_MODEL)
    if _model is not None and _model_name == name:
        return _model
    from app.retrieval.hf import ensure

    ensure()
    from sentence_transformers import CrossEncoder
    _model = CrossEncoder(name, device="mps", max_length=512)
    _model_name = name
    return _model


def rerank(query: str, hits: list, top: int = 40) -> list:
    """按 cross-encoder 分重排前 top 条，其余原样接在后面。失败时返回原列表。"""
    if len(hits) < 2:
        return hits
    try:
        m = _load()
        head, tail = hits[:top], hits[top:]
        texts = [(f"{h.clause.title}｜{h.clause.text}")[:1200] for h in head]
        scores = m.predict([(query, t) for t in texts], batch_size=16, show_progress_bar=False)
        for i, h in enumerate(head):
            h._rr = float(scores[i])       # 决策层要用同一把尺子
        order = sorted(range(len(head)), key=lambda i: -float(scores[i]))
        return [head[i] for i in order] + tail
    except Exception:                 # 模型缺失/显存不足都不该让检索挂掉
        return hits
