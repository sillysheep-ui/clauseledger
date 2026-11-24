"""重排层：用 cross-encoder 给 (问题, 条款) 打分，做检索链路的最终排序。

为什么放在 _collapse 之前：条款级才有足够信号。把同一份文件压成一条再重排，
喂给模型的是"这份文件最像问题的那一条"，而它未必是能回答问题的条款——实测
在 collapse 之后重排会把 hit@1 从 58.7% 打到 41.3%。

配置：
  CLAUSELEDGER_RERANK=0              关闭（默认开）
  CLAUSELEDGER_RERANK_MODEL=...      默认 BAAI/bge-reranker-v2-m3
  CLAUSELEDGER_RERANK_TOP=40         重排前 N 条条款
  CLAUSELEDGER_RERANK_MAXLEN=8192    单条最大 token；默认按模型定（v2-m3 8192，其他 512）

输入不再做字符截断：条款是原子单位，截断交给 tokenizer，按模型上限走。
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
# 本地优先：ModelScope 下的权重放在仓库外，避免每次走 HF（国内常被 LFS 域名挡住）
_LOCAL = Path(__file__).resolve().parents[2] / "data" / "models" / "bge-reranker-v2-m3"
_model = None
_model_name: str | None = None


def enabled() -> bool:
    """默认开启：重排是标准 RAG 流程的一环，不再按单次评测结果决定开关。

    在我们自己的 hybrid 候选池上它没有正收益（P@1 51.0% → 40.6%，+1.84s/题），
    这条实测记在 docs/EVAL-SPEC.md §5.1；要复现旧口径的数字用
    `CLAUSELEDGER_RERANK=0` 关掉，而不是改默认值。
    """
    return os.environ.get("CLAUSELEDGER_RERANK", "1") not in ("", "0", "false", "no")


def top_n() -> int:
    try:
        return int(os.environ.get("CLAUSELEDGER_RERANK_TOP", "40"))
    except ValueError:
        return 40


def max_len(name: str) -> int:
    """单条最大 token：默认按模型定，可用环境变量覆盖。"""
    env = os.environ.get("CLAUSELEDGER_RERANK_MAXLEN")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return 8192 if "v2-m3" in name else 512


def model_id() -> str:
    """显式环境变量 > 本地目录 > HF 模型名。"""
    env = os.environ.get("CLAUSELEDGER_RERANK_MODEL")
    if env:
        return env
    if (_LOCAL / "model.safetensors").exists():
        return str(_LOCAL)
    return _DEFAULT_MODEL


def _load():
    global _model, _model_name
    name = model_id()
    if _model is not None and _model_name == name:
        return _model
    from app.retrieval.hf import ensure

    ensure()
    from sentence_transformers import CrossEncoder
    _model = CrossEncoder(name, device="mps", max_length=max_len(name))
    _model_name = name
    return _model


def _predict(m, pairs: list[tuple[str, str]], batch_size: int = 8) -> list[float]:
    """按长度分桶打分，避免一条长条款把整批 padding 到最长；结果保持原顺序。"""
    order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][1]))
    scores = [0.0] * len(pairs)
    for i in range(0, len(order), batch_size):
        idx = order[i:i + batch_size]
        out = m.predict([pairs[j] for j in idx], batch_size=len(idx), show_progress_bar=False)
        for j, s in zip(idx, out):
            scores[j] = float(s)
    return scores


def rerank(query: str, hits: list, top: int = 40) -> list:
    """按 cross-encoder 分重排前 top 条，其余原样接在后面。失败时返回原列表。"""
    if len(hits) < 2:
        return hits
    try:
        m = _load()
        head, tail = hits[:top], hits[top:]
        # 不按字符截断：条款完整交给模型，上限由 tokenizer(max_length) 决定
        texts = [f"{h.clause.title}｜{h.clause.text}" for h in head]
        scores = _predict(m, [(query, t) for t in texts])
        for i, h in enumerate(head):
            h._rr = float(scores[i])       # 决策层要用同一把尺子
        order = sorted(range(len(head)), key=lambda i: -float(scores[i]))
        return [head[i] for i in order] + tail
    except Exception:                 # 模型缺失/显存不足都不该让检索挂掉
        return hits
