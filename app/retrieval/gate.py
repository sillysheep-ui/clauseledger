"""准出判据：这条条款到底是不是在回答这个问题。

原来判据是"词面分 + 分差 + 覆盖率"的组合，阈值 5.3/1.25 是 BM25 量纲，会随
问法长短漂移（同一件事加半句话，分数就变）。这里改成**语义相似度**：0–1、跨
问法可比、一句话能讲清。

口径：前 3 条里**最像**的那条 ≥ 阈值就放行。为什么看前 3 条？装配本来就按
"前 3 份文件"展开，第 2/3 条足够像时它同样会进上下文、同样能回答问题。

阈值 0.68 是在 dev 上选的（可答题 top-3 最大余弦 p10 = 0.691，无政策题上限
0.761 与可答题重叠），换语料/换模型要重选。它不是概率，是工作点。
"""
from __future__ import annotations

import os

COS_MIN = float(os.environ.get("CLAUSELEDGER_GATE_COS", "0.68"))
K = 3


def enabled() -> bool:
    return os.environ.get("CLAUSELEDGER_GATE", "legacy") == "cos"


def semantic_ok(query: str, hits, k: int = K) -> bool | None:
    """前 k 条的最大语义余弦是否过线；模型不可用返回 None（调用方退回旧判据）。"""
    if not hits:
        return False
    from app.retrieval import dense

    if not dense.available():
        return None
    try:
        model = dense._model()
        texts = [getattr(h, "child", None) or h.clause.text for h in hits[:k]]
        v = model.encode([query] + texts, normalize_embeddings=True)
        best = max(float(v[0] @ v[i + 1]) for i in range(len(texts)))
        return best >= COS_MIN
    except Exception:
        return None
