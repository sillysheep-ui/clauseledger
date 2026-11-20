"""BM25 参数扫描：只动 k1 / b，不动切分、不加任何后处理。

依据：之前的失败分析显示"正确条款中位 261 字，压过它的 top-1 中位 107 字"，
典型长度归一化过度惩罚。BEIR 的 BM25 基线用 k1=0.9 / b=0.4，我们沿用 bm25s 默认
k1=1.5 / b=0.75。这一轮只扫这两个参数，看 hit@1 能否靠"少惩罚长条款"提上去。

口径与 recall_compare 的 A 完全一致（子块索引、按 pid 取最高分、_collapse、
_prefer_effective、is_hit），只是重建索引时换参数。

用法：
  python -m tools.experiments.bm25_params
  python -m tools.experiments.bm25_params --grid k1=0.9,1.5 b=0.3,0.4,0.5,0.75
"""
from __future__ import annotations

import argparse
import json
import time
from itertools import product
from pathlib import Path

import bm25s

from app.retrieval.index import tokenize
from tools.experiments.recall_compare import (
    bm25_rank,
    evaluate,
    load_corpus,
    mcnemar,
    to_pids,
)


def build(texts: list[str], k1: float, b: float, method: str = "lucene"):
    corpus = [tokenize(t) for t in texts]
    r = bm25s.BM25(k1=k1, b=b, method=method)
    r.index(corpus)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="k1=1.5 b=0.3,0.4,0.5,0.75")
    ap.add_argument("--out", default="eval/reports/bm25-params.json")
    a = ap.parse_args()

    grid: dict[str, list[float]] = {}
    for part in a.grid.split():
        k, v = part.split("=")
        grid[k] = [float(x) for x in v.split(",")]

    clauses, children, qs = load_corpus()
    texts = [c["text"] for c in children]
    print(f"语料：{len(clauses)} 条款 / {len(children)} 子块 / dev 官方题 {len(qs)} 条", flush=True)

    results = []
    base = None
    for k1, b in product(grid.get("k1", [1.5]), grid.get("b", [0.75])):
        t0 = time.perf_counter()
        r = build(texts, k1, b)
        dt = time.perf_counter() - t0
        ranked, lat = [], []
        for q in qs:
            t1 = time.perf_counter()
            ranked.append((to_pids(bm25_rank(r, children, q["q"], use_bigram=False), children), None))
            lat.append(time.perf_counter() - t1)
        m = evaluate(f"k1={k1} b={b}", ranked, clauses, qs, lat)
        n = m["n"]
        m.update({"k1": k1, "b": b, "build_s": dt,
                  "hit1_pct": 100.0 * m["hit1"] / n, "hit3_pct": 100.0 * m["hit3"] / n})
        results.append(m)
        if base is None:
            base = m
        print(f"k1={k1} b={b}: hit@1 {m['hit1']}/{n}={m['hit1_pct']:.1f}%  "
              f"hit@3 {m['hit3_pct']:.1f}%  MRR {m['mrr']:.3f}  "
              f"找到 {m['found']}  建索引 {dt:.1f}s", flush=True)

    ref = next((m for m in results if m["k1"] == 1.5 and m["b"] == 0.75), base)
    for m in results:
        mc = mcnemar(ref, m)
        m["vs_default"] = mc
        print(f"  vs 默认(1.5/0.75)：改好 {mc['better']} / 改坏 {mc['worse']}，p={mc['p']:.3f}")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [{k: v for k, v in m.items() if k != "rows"} for m in results],
        ensure_ascii=False, indent=2), "utf-8")
    print("→", out)


if __name__ == "__main__":
    main()
