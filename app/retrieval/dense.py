"""稠密召回（可选）：给父子索引加一路语义检索，用 RRF 与 BM25 融合。

为什么放在这里而不是替换 BM25：

* 实测（docs/RECALL-COMPARE.md）单独用 dense 并不比 BM25 好：56.5% vs 55.4%，
  配对检验 p=1.000。它的价值只在"和 BM25 看到不同的候选"时——而我们的实验里
  两路候选的并集就是 BM25 本身（tools/experiments/rerank_pool.py），
  所以融合能带来的上限很小，收益主要落在"第 2、3 名互换"这类排序噪声上。

* 代价是真实的：模型 95 MB、6,064 个子块编码约 71 秒、单查询 30–70 毫秒
  （BM25 是 2 毫秒）。所以默认不开，`CLAUSELEDGER_RECALL=hybrid` 才启用。

* 模型缺失、依赖没装、缓存损坏，一律**降级回 BM25** 并把实际模式写进
  `Answer.searched["recall"]`——审计要求：用了哪路召回必须是记录，不是默认值。

缓存：`data/index/dense_children.npz`，键是 `children.jsonl` 的 sha256 前 12 位。
语料一改，sha 变，缓存自动失效重算（不做"猜"的失效逻辑）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

INDEX = Path(os.environ.get("CLAUSELEDGER_INDEX", "data/index"))
MODEL = os.environ.get("CLAUSELEDGER_DENSE_MODEL", "BAAI/bge-small-zh-v1.5")
CACHE = INDEX / "dense_children.npz"
RRF_K = int(os.environ.get("CLAUSELEDGER_RRF_K", "60"))

_MODEL = None          # 懒加载：没开 hybrid 时连 import 都不做
_MATRIX: np.ndarray | None = None
_CACHE_KEY: str = ""


def children_key(children: list[dict]) -> str:
    """子块集合的指纹：内容变了，向量缓存就必须重算。"""
    h = hashlib.sha256()
    for c in children:
        # 也把 index_text 算进指纹：同一条子块在"开前缀/不开前缀"下是不同的索引对象
        h.update(f"{c['cid']}|{c['text']}|{c.get('index_text','')}".encode())
    return h.hexdigest()[:12]


def available() -> bool:
    """能不能真的跑稠密召回：依赖在、模型在（或能第一次拉下来）。"""
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    from app.retrieval.hf import ensure

    ensure()
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return True
    except Exception:
        return False


def _model():
    global _MODEL
    if _MODEL is None:
        from app.retrieval.hf import ensure

        ensure()
        from sentence_transformers import SentenceTransformer
        device = "mps" if os.environ.get("CLAUSELEDGER_DEVICE", "mps") == "mps" else "cpu"
        _MODEL = SentenceTransformer(MODEL, device=device)
    return _MODEL


def matrix(children: list[dict]) -> np.ndarray:
    """子块向量矩阵，带磁盘缓存。维度 (n, 512)，已 L2 归一化。"""
    global _MATRIX, _CACHE_KEY
    key = children_key(children)
    if _MATRIX is not None and _CACHE_KEY == key:
        return _MATRIX
    if CACHE.exists():
        try:
            z = np.load(CACHE, allow_pickle=False)
            if str(z["key"]) == key:
                _MATRIX, _CACHE_KEY = z["emb"].astype(np.float32), key
                return _MATRIX
        except Exception:
            pass  # 缓存坏了就当没有，重算一次
    # 忠实复刻 Anthropic/STARA：上下文要**同时**进向量索引，否则只测了 BM25 一侧
    use_prefix = os.environ.get("CLAUSELEDGER_DENSE_PREFIX", "1") not in ("", "0", "false", "no")
    texts = [(c.get("index_text") or c["text"]) if use_prefix else c["text"] for c in children]
    emb = _model().encode(texts, normalize_embeddings=True,
                          batch_size=32, show_progress_bar=False)
    _MATRIX, _CACHE_KEY = np.asarray(emb, dtype=np.float32), key
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, emb=_MATRIX, key=key)
    return _MATRIX


def rank(query: str, children: list[dict], k: int = 60) -> list[tuple[int, float]]:
    """返回 [(子块下标, 余弦)]，按分数降序。"""
    mat = matrix(children)
    q = _model().encode([query], normalize_embeddings=True)[0]
    sims = mat @ q
    k = min(k, len(sims))
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [(int(i), float(sims[i])) for i in top]


def rrf(a: list[int], b: list[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：只用名次，不用分数。

    RRF(d) = Σ 1/(k + rank_i(d))。k=60 是原论文的默认值，作用是压住
    "第一名特别突出"的效应——我们的两路分数量纲完全不同（BM25 能到 10+，
    余弦只有 0-1），直接相加必须先归一化，而 RRF 绕开了这件事。
    """
    score: dict[int, float] = {}
    for lst in (a, b):
        for r, cid in enumerate(lst, start=1):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + r)
    return sorted(score.items(), key=lambda kv: -kv[1])


def info() -> dict:
    return {"model": MODEL, "cache": str(CACHE), "rrf_k": RRF_K,
            "cache_exists": CACHE.exists()}
