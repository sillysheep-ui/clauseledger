"""决策层的置信度：把"词对上了"和"意思对上了"放在一起看。

原来的判据只看 BM25 分和分差，于是重排/稠密把"语义对、用词不同"的条款顶到
第一时，判据反而说"不敢答"——越有效的语义层，越被自己的判据吃掉。

实测（dev 114 + 口语 64，AUC 看"top-1 是否正确"）：

    BM25 分   0.731
    分差      0.659
    语义余弦  0.704
    三者组合  0.767   ← 比任何单一信号都好

所以置信度是三项的加权和，每一项先归一到 0–1：

    lex     = min(BM25 分 / 12, 1)         词对上了多少
    margin  = min(max(分差, 0) / 4, 1)     领先第二名多少
    cos     = (余弦 − 0.55) / 0.35         意思对上了多少（bge-small 的典型区间）

阈值 0.38 是在 dev 上选的，硬约束是"域外 / 域内无政策两类负样本一条都不能答"；
它不是校准概率，只是当前语料上的工作点，换语料要重选。

重排打开时（`_rr` 存在）：`score()` 收到第 4 项 `rr = sigmoid(重排分)`，改用
`0.45·rr + 0.20·lex + 0.10·margin + 0.25·cos`，阈值默认 **0.50**
（`CLAUSELEDGER_CONF_T_RR`）。理由：排序换了尺子，判据必须跟着换，否则重排把
"语义对、BM25 分低"的条款顶到第一时，判据会说"不敢答"。

为什么不用覆盖率：覆盖率在这批题上是反的（该答的 0.38–0.45，该拒的 0.50–0.67）。
"""
from __future__ import annotations

import os

LEX_SCALE = 12.0
MARGIN_SCALE = 4.0
COS_LO = 0.55
COS_SPAN = 0.35
W_LEX, W_MARGIN, W_COS = 0.40, 0.25, 0.35
# 重排打开时：重排分权重最高，因为它就是"这条条款能不能回答问题"的直接判断。
W_RR, W_RR_LEX, W_RR_MARGIN, W_RR_COS = 0.45, 0.20, 0.10, 0.25
DEFAULT_THRESHOLD = 0.38
DEFAULT_THRESHOLD_RR = 0.50


def enabled() -> bool:
    return os.environ.get("CLAUSELEDGER_CONF", "1") not in ("0", "false", "no")


def threshold(rr: bool = False) -> float:
    key = "CLAUSELEDGER_CONF_T_RR" if rr else "CLAUSELEDGER_CONF_T"
    try:
        return float(os.environ.get(key, DEFAULT_THRESHOLD_RR if rr else DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD_RR if rr else DEFAULT_THRESHOLD


def score(lex: float, margin: float, cos: float, rr: float | None = None) -> float:
    """rr = sigmoid(重排分)，0–1。给了它就用另一组权重（排序和判据同一把尺子）。"""
    a = min(max(lex, 0.0) / LEX_SCALE, 1.0)
    b = min(max(margin, 0.0) / MARGIN_SCALE, 1.0)
    c = min(max((cos - COS_LO) / COS_SPAN, 0.0), 1.0)
    if rr is not None:
        e = min(max(rr, 0.0), 1.0)
        return round(W_RR * e + W_RR_LEX * a + W_RR_MARGIN * b + W_RR_COS * c, 4)
    return round(W_LEX * a + W_MARGIN * b + W_COS * c, 4)


def cosine(query: str, text: str) -> float | None:
    """问题与条款的语义相似度；模型不可用就返回 None（调用方退回旧规则）。"""
    if not enabled():
        return None
    try:
        from app.retrieval import dense

        if not dense.available():
            return None
        m = dense._model()
        v = m.encode([query, text[:400]], normalize_embeddings=True)
        return float(v[0] @ v[1])
    except Exception:
        return None
