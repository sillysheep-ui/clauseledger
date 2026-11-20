"""召回方式对比：同一批题、同一份语料，只换召回器。

四种方式：
  A  bm25       —— 现状：jieba 分词，索引建在子块上，按 pid 取最高子块分
  B  bm25+bi    —— 分词再叠字二元组（中文分词歧义的对冲）
  C  dense      —— BAAI/bge-small-zh-v1.5，本地推理，余弦相似度
  D  hybrid     —— A 与 C 的 RRF（只看名次，避免量纲不可比）

口径（刻意保持朴素，才能把"换召回"的净效果量出来）：
- 四种方式都只按自己的分数排序，**不加标题加权、不加效力加成**；
- 映射到父条款后，统一走 search.py 的 _collapse / _prefer_effective；
- 指标是 hit@1 / hit@3 / MRR，金标判据复用 tools/eval_v2.py::is_hit；
- 另跑一行 "A+后处理"（直接调 search_result），用来锚定线上口径（hit@1 58%）。

用法：
  python -m tools.experiments.recall_compare                # 跑全部
  python -m tools.experiments.recall_compare --methods A,B  # 只跑两路
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import tracemalloc
from pathlib import Path

import numpy as np

from app.retrieval.index import INDEX, tokenize
from app.retrieval.records import Clause
from app.retrieval.search import Hit, _collapse, _prefer_effective

QS = Path(os.environ.get("CLAUSELEDGER_QUESTIONS", "eval/questions_v2.json"))
REPORTS = Path("eval/reports")
CACHE = INDEX / "dense_children.npz"
MODEL = "BAAI/bge-small-zh-v1.5"
TOPK = 200          # 子块候选数；够大，避免"映射到 pid 后不够 10 条"
RRF_K = 60


# ---------- 语料 ----------

def load_corpus(splits: set[str] = frozenset({"dev"})) -> tuple[list[Clause], list[dict], list[dict]]:
    clauses = [Clause(**json.loads(l)) for l in
               (INDEX / "clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    children = [json.loads(l) for l in
                (INDEX / "children.jsonl").read_text("utf-8").splitlines() if l.strip()]
    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q.get("split") in splits and q.get("category") == "official"
          and not q.get("ambiguous") and not q.get("stale")]
    return clauses, children, qs


# ---------- 打分器 ----------

def _bigrams(text: str) -> list[str]:
    import re
    s = re.sub(r"[\s\u3000，。、；：！？（）()《》〈〉“”\"'·—…,.;:!?\[\]【】]+", "", text)
    return [s[i:i + 2] for i in range(len(s) - 1)
            if all("\u4e00" <= ch <= "\u9fa5" for ch in s[i:i + 2])]


def build_bm25(texts: list[str], use_bigram: bool):
    import bm25s
    corpus = []
    for t in texts:
        toks = tokenize(t)
        if use_bigram:
            toks = toks + _bigrams(t)
        corpus.append(toks)
    r = bm25s.BM25()
    r.index(corpus)
    return r, corpus


def bm25_rank(retriever, children, query: str, use_bigram: bool) -> list[int]:
    qt = tokenize(query)
    if use_bigram:
        qt = qt + _bigrams(query)
    idx, scores = retriever.retrieve([qt], k=min(len(children), TOPK), show_progress=False)
    return list(zip([int(i) for i in idx[0]], [float(s) for s in scores[0]]))


def dense_matrix(children: list[dict], device: str = "mps") -> tuple[np.ndarray, float]:
    """编码子块并缓存。返回 (归一化矩阵, 建索引耗时秒)。"""
    if CACHE.exists():
        z = np.load(CACHE)
        if int(z["n"]) == len(children):
            return z["m"], 0.0
    from sentence_transformers import SentenceTransformer
    t0 = time.perf_counter()
    m = SentenceTransformer(MODEL, device=device)
    vecs = m.encode([c["text"] for c in children], batch_size=64,
                    normalize_embeddings=True, show_progress_bar=False)
    dt = time.perf_counter() - t0
    arr = np.asarray(vecs, dtype=np.float32)
    np.savez_compressed(CACHE, m=arr, n=np.array(len(children)))
    return arr, dt


def dense_rank(matrix: np.ndarray, children: list[dict], query: str) -> list[tuple[int, float]]:
    from sentence_transformers import SentenceTransformer
    if not hasattr(dense_rank, "_m"):
        dense_rank._m = SentenceTransformer(MODEL, device="mps")
    q = dense_rank._m.encode([query], normalize_embeddings=True)[0]
    sims = matrix @ q
    top = np.argsort(-sims)[:TOPK]
    return [(int(i), float(sims[i])) for i in top]


def to_pids(pairs: list[tuple[int, float]], children: list[dict]) -> list[tuple[int, float]]:
    best: dict[int, float] = {}
    for ci, s in pairs:
        pid = int(children[ci]["pid"])
        if s > best.get(pid, -1e9):
            best[pid] = s
    return sorted(best.items(), key=lambda x: -x[1])


def rrf(a: list[tuple[int, float]], c: list[tuple[int, float]]) -> list[tuple[int, float]]:
    score: dict[int, float] = {}
    for rank, (ci, _) in enumerate(a, 1):
        score[ci] = score.get(ci, 0.0) + 1.0 / (RRF_K + rank)
    for rank, (ci, _) in enumerate(c, 1):
        score[ci] = score.get(ci, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(score.items(), key=lambda x: -x[1])


# ---------- 评测 ----------

def rank_of(hits: list[Hit], q: dict) -> int:
    from tools.eval_v2 import is_hit
    for i, h in enumerate(hits, 1):
        if is_hit(h, q["gold_doc"], q["gold_title"]):
            return i
    return 0


def evaluate(name: str, ranked: list[tuple[int, float]], clauses: list[Clause],
             qs: list[dict], latency: list[float]) -> dict:
    rows = []
    for q, (order, _) in zip(qs, ranked):
        hits = [Hit(clauses[pid], score) for pid, score in order]
        hits = _prefer_effective(_collapse(hits))
        r = rank_of(hits, q)
        rows.append({"id": q["id"], "rank": r})
    n = len(rows)
    h1 = sum(1 for r in rows if r["rank"] == 1)
    h3 = sum(1 for r in rows if 1 <= r["rank"] <= 3)
    mrr = sum(1 / r["rank"] if r["rank"] else 0.0 for r in rows) / max(1, n)
    mean_rank = sum(r["rank"] for r in rows if r["rank"]) / max(1, sum(1 for r in rows if r["rank"]))
    lat = sorted(latency)
    return {"method": name, "n": n, "hit1": h1, "hit3": h3, "mrr": mrr,
            "found": sum(1 for r in rows if r["rank"]),
            "mean_rank": mean_rank, "rows": rows,
            "lat_med_ms": (lat[len(lat) // 2] * 1000) if lat else 0.0,
            "lat_p90_ms": (lat[int(len(lat) * 0.9)] * 1000) if lat else 0.0}


def mcnemar(a: dict, b: dict) -> dict:
    """配对比较：只看 top-1 对错不同的题。返回 better/worse 与精确二项 p。"""
    ra = {r["id"]: r["rank"] == 1 for r in a["rows"]}
    rb = {r["id"]: r["rank"] == 1 for r in b["rows"]}
    better = sum(1 for k in ra if rb[k] and not ra[k])
    worse = sum(1 for k in ra if ra[k] and not rb[k])
    n = better + worse
    if n == 0:
        return {"better": 0, "worse": 0, "p": 1.0}
    k = min(better, worse)
    p = 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"better": better, "worse": worse, "p": min(1.0, p)}


def production_reference(qs: list[dict]) -> dict:
    """线上口径（含标题加权、闸门）：用来锚定 hit@1 58%，不参与方法比较。"""
    from app.retrieval.search import search
    rows = []
    for q in qs:
        hits = search(q["q"], k=10)
        rows.append({"id": q["id"], "rank": rank_of(hits, q)})
    return {"method": "A+后处理(线上)", "n": len(rows),
            "hit1": sum(1 for r in rows if r["rank"] == 1),
            "hit3": sum(1 for r in rows if 1 <= r["rank"] <= 3),
            "mrr": sum(1 / r["rank"] if r["rank"] else 0.0 for r in rows) / max(1, len(rows)),
            "found": sum(1 for r in rows if r["rank"]),
            "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="A,B,C,D")
    ap.add_argument("--out", default="")
    ap.add_argument("--split", default="dev", help="dev 或 dev+test")
    a = ap.parse_args()
    want = {m.strip().upper() for m in a.methods.split(",") if m.strip()}

    splits = {x.strip() for x in a.split.split("+") if x.strip()}
    clauses, children, qs = load_corpus(splits)
    print(f"语料：{len(clauses)} 条款 / {len(children)} 子块 / dev 题 {len(qs)} 条", flush=True)

    tracemalloc.start()
    results: dict[str, dict] = {}
    timings: dict[str, float] = {}

    bm25_a = bm25_b = None
    if {"A", "B", "D"} & want:
        t0 = time.perf_counter()
        bm25_a, _ = build_bm25([c["text"] for c in children], False)
        timings["A_build"] = time.perf_counter() - t0
    if {"B"} & want:
        t0 = time.perf_counter()
        bm25_b, _ = build_bm25([c["text"] for c in children], True)
        timings["B_build"] = time.perf_counter() - t0

    mat = None
    if {"C", "D"} & want:
        mat, dt = dense_matrix(children)
        timings["C_build"] = dt

    ranked: dict[str, list] = {}

    def run_one(name: str, fn) -> None:
        out, lat = [], []
        for q in qs:
            t1 = time.perf_counter()
            order = to_pids(fn(q["q"]), children)
            lat.append(time.perf_counter() - t1)
            out.append((order, q["q"]))
        ranked[name] = out
        r = evaluate(name, out, clauses, qs, lat)
        r["build_s"] = timings.get(f"{name}_build", timings.get(f"{name[0]}_build", 0.0))
        results[name] = r
        print(f"  {name}: hit@1 {r['hit1']}/{r['n']} = {r['hit1']/r['n']:.1%} "
              f"hit@3 {r['hit3']/r['n']:.1%} MRR {r['mrr']:.3f} "
              f"延迟中位 {r['lat_med_ms']:.0f}ms", flush=True)

    if "A" in want:
        run_one("A", lambda q: bm25_rank(bm25_a, children, q, False))
    if "B" in want:
        run_one("B", lambda q: bm25_rank(bm25_b, children, q, True))
    if "C" in want:
        run_one("C", lambda q: dense_rank(mat, children, q))
    if "D" in want:
        def _d(q: str):
            return rrf(bm25_rank(bm25_a, children, q, False), dense_rank(mat, children, q))
        run_one("D", _d)

    peak = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()

    if "A" in results:
        results["A+后处理(线上)"] = production_reference(qs)

    print("\n| 方法 | hit@1 | hit@3 | MRR | 找到金标 | 平均名次 | 中位延迟 | 建索引 |",
          flush=True)
    print("|---|---|---|---|---|---|---|---|")
    for k, r in results.items():
        n = r["n"]
        print(f"| {k} | {r['hit1']}/{n} = {r['hit1']/n:.1%} | {r['hit3']/n:.1%} | "
              f"{r.get('mrr', 0):.3f} | {r.get('found', '—')}/{n} | "
              f"{r.get('mean_rank', 0):.2f} | {r.get('lat_med_ms', 0):.0f}ms | "
              f"{r.get('build_s', 0):.0f}s |")

    base = results.get("A")
    if base:
        print("\n配对比较（vs A，只看 top-1 对错不同的题）：", flush=True)
        for k in ("B", "C", "D"):
            if k in results:
                m = mcnemar(base, results[k])
                print(f"  A vs {k}: 改好 {m['better']} / 改坏 {m['worse']} / p={m['p']:.4f}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) if a.out else REPORTS / f"recall-{ts}.json"
    out.write_text(json.dumps({
        "ts": ts, "model": MODEL, "topk": TOPK, "rrf_k": RRF_K,
        "corpus": {"clauses": len(clauses), "children": len(children)},
        "peak_mb": peak,
        "summary": {k: {kk: vv for kk, vv in v.items() if kk != "rows"}
                    for k, v in results.items()},
        "mcnemar": {k: mcnemar(base, results[k]) for k in ("B", "C", "D") if k in results} if base else {},
        "rows": {k: v["rows"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"\n→ {out}")
    print(f"峰值内存 +{peak:.0f} MB")


if __name__ == "__main__":
    main()
