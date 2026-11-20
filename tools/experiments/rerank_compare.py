"""重排对比：召回不动，只换排序器。

问题：四路召回都没显著赢过 BM25，但 R@10 已 90%、R@50 97% —— 说明金标基本
都在候选池里，差的是"排到第一"。所以这一轮只测重排。

口径：
- 检索固定为 A（BM25 + jieba，建在子块、按 pid 取最高分），取前 --cands 条父条款；
- 重排用 cross-encoder（默认 BAAI/bge-reranker-base）打 (query, 条款全文) 的分；
- 只按重排分排序，不加标题加权、不加效力加成；统一走 _collapse / _prefer_effective；
- 指标 hit@1 / hit@3 / MRR + Wilson，与 A 做配对 McNemar（≥6:0 才算过关）。

已知偏差（写进报告，不藏）：
- cross-encoder max_length 512 token，长条款会被截断；
- 条款正文里含"附件N ›"前缀与编号，未做清洗。

用法：
  python -m tools.experiments.rerank_compare --cands 30
  python -m tools.experiments.rerank_compare --model BAAI/bge-reranker-v2-m3
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.retrieval.records import Clause
from tools.experiments.recall_compare import (
    bm25_rank,
    build_bm25,
    evaluate,
    load_corpus,
    mcnemar,
    production_reference,
    to_pids,
)

REPORTS = Path("eval/reports")
DEFAULT_MODEL = "BAAI/bge-reranker-base"


def rerank_scores(model, query: str, clauses: list[Clause]) -> list[float]:
    pairs = [(query, c.text[:1200]) for c in clauses]
    return [float(x) for x in model.predict(pairs, batch_size=16, show_progress_bar=False)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cands", type=int, default=30, help="参与重排的父条款数")
    ap.add_argument("--out", default="")
    ap.add_argument("--split", default="dev", help="dev 或 dev+test")
    a = ap.parse_args()

    splits = {x.strip() for x in a.split.split("+") if x.strip()}
    clauses, children, qs = load_corpus(splits)
    print(f"语料：{len(clauses)} 条款 / {len(children)} 子块 / dev 官方题 {len(qs)} 条", flush=True)

    retriever, _ = build_bm25([c["text"] for c in children], use_bigram=False)

    # 基线 A：召回后直接按 BM25 排
    base_ranked, lat_a = [], []
    for q in qs:
        t0 = time.perf_counter()
        pairs = bm25_rank(retriever, children, q["q"], use_bigram=False)
        order = to_pids(pairs, children)
        base_ranked.append((order, None))
        lat_a.append(time.perf_counter() - t0)
    base = evaluate("A BM25", base_ranked, clauses, qs, lat_a)

    from sentence_transformers import CrossEncoder
    t0 = time.perf_counter()
    model = CrossEncoder(a.model, device="mps", max_length=512)
    load_s = time.perf_counter() - t0
    print(f"重排模型 {a.model} 加载 {load_s:.1f}s", flush=True)

    rr_ranked, lat_r, kept = [], [], 0
    for q in qs:
        t0 = time.perf_counter()
        pairs = bm25_rank(retriever, children, q["q"], use_bigram=False)
        order = to_pids(pairs, children)[: a.cands]
        cand = [clauses[pid] for pid, _ in order]
        if len(cand) > 1:
            sc = rerank_scores(model, q["q"], cand)
            order = sorted(zip([p for p, _ in order], sc), key=lambda x: -x[1])
            kept += 1
        rr_ranked.append((order, None))
        lat_r.append(time.perf_counter() - t0)

    rer = evaluate("E BM25+rerank", rr_ranked, clauses, qs, lat_r)

    for m in (base, rer):
        n = m["n"]
        m["hit1_pct"] = 100.0 * m["hit1"] / n
        m["hit3_pct"] = 100.0 * m["hit3"] / n

    mc = mcnemar(base, rer)
    prod = None
    try:
        prod = production_reference(qs)
    except Exception as e:                       # 线上口径只是参照，失败不影响结论
        print("production_reference 跳过：", type(e).__name__, e)

    print("\n方法              hit@1        hit@3     MRR    找到  平均名次  延迟中位")
    for m in (base, rer):
        print(f"{m['method']:<16} {m['hit1']}/{m['n']}={m['hit1_pct']:.1f}%  "
              f"{m['hit3_pct']:.1f}%  {m['mrr']:.3f}  {m['found']}  "
              f"{m['mean_rank']:.2f}  {m['lat_med_ms']:.0f} ms")
    if prod:
        print(f"{prod['method']:<16} {prod['hit1']}/{prod['n']}={100*prod['hit1']/prod['n']:.1f}%  "
              f"{100*prod['hit3']/prod['n']:.1f}%  {prod['mrr']:.3f}  {prod['found']}")
    print(f"\n配对 McNemar（rerank vs A）：改好 {mc['better']} / 改坏 {mc['worse']}，p={mc['p']:.3f}")
    print(f"重排候选数 {a.cands}，实际重排 {kept} 题；模型加载 {load_s:.1f}s")

    delta = {r["id"]: r["rank"] for r in base["rows"]}
    for r in rer["rows"]:
        r["before"] = delta.get(r["id"], 0)

    out = Path(a.out) if a.out else REPORTS / "rerank.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": a.model, "cands": a.cands, "load_s": load_s,
        "base": {k: v for k, v in base.items()},
        "rerank": {k: v for k, v in rer.items()},
        "mcnemar": mc, "production": prod,
    }, ensure_ascii=False, indent=2), "utf-8")
    print("→", out)


if __name__ == "__main__":
    main()
