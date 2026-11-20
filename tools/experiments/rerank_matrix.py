"""重排到底行不行：一个口径跑完三种输入文本。

之前两次实验的分母不一样（一次 106 道"有候选的题"，一次 107 道完整流水线），
数字没法并排看。这里固定：同一批题（dev+test）、同一个 BM25 top-50 候选、
同一个金标（文号）、同一个分母，只改重排输入文本。

    A  BM25 原序（基线）
    B  重排：条款原文
    C  重排：条款 + 父条首句

指标：池内含金标率、P@1（全）、P@1|池内、MRR。
"""
from __future__ import annotations

import argparse
import statistics
import time

import numpy as np

from tools.experiments.recall_compare import bm25_rank, build_bm25, load_corpus, to_pids

MODEL = "BAAI/bge-reranker-base"
DEPTH = 50


def parent_first_sentence(clauses, c):
    if len(c.path) < 2:
        return ""
    want = tuple(c.path[:-1])
    for x in clauses:
        if x.doc_no == c.doc_no and tuple(x.path) == want and x.start <= c.start <= max(x.end, c.start):
            return x.text.split("。")[0][:120]
    return ""


def metrics(orders: list[list[str]], golds: list[str]) -> dict:
    n = len(golds)
    pool = [g in o for o, g in zip(orders, golds)]
    p1 = [bool(o) and o[0] == g for o, g in zip(orders, golds)]
    rr = [1.0 / (o.index(g) + 1) if g in o else 0.0 for o, g in zip(orders, golds)]
    idx = [i for i, ok in enumerate(pool) if ok]
    p1_in = [p1[i] for i in idx]
    return {"n": n, "pool": sum(pool) / n, "p1": sum(p1) / n,
            "p1_in": (sum(p1_in) / len(idx)) if idx else 0.0,
            "mrr": statistics.mean(rr)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="dev,test")
    a = ap.parse_args()
    splits = set(a.splits.split(","))

    clauses, children, qs = load_corpus(splits)
    print(f"题 {len(qs)} 道 / 条款 {len(clauses)} / 子块 {len(children)}")
    texts = [c["text"] for c in children]
    retriever, _ = build_bm25(texts, False)

    questions = [q.get("q_ctx") or q["q"] for q in qs]
    golds = [q["gold_doc"] for q in qs]

    base_orders, pools = [], []
    t0 = time.time()
    for q in questions:
        pairs = bm25_rank(retriever, children, q, False)[:DEPTH]
        pids = [p for p, _ in to_pids(pairs, children)]
        docs = [clauses[p].doc_no for p in pids if 0 <= p < len(clauses)]
        seen, order = set(), []
        for d in docs:
            if d not in seen:
                seen.add(d)
                order.append(d)
        base_orders.append(order)
        pools.append((pids, q))
    print(f"候选构建 {time.time() - t0:.1f}s")

    from sentence_transformers import CrossEncoder
    ce = CrossEncoder(MODEL, device="mps")

    def rerank_variant(build_text) -> tuple[list[list[str]], float]:
        rows, t = [], time.time()
        for (pids, q) in pools:
            cand = [clauses[p] for p in pids if 0 <= p < len(clauses)][:DEPTH]
            if not cand:
                rows.append([])
                continue
            pairs = [[q, build_text(c)] for c in cand]
            scores = ce.predict(pairs, batch_size=32, show_progress_bar=False)
            order = [cand[i].doc_no for i in np.argsort(-np.asarray(scores))]
            seen, ded = set(), []
            for d in order:
                if d not in seen:
                    seen.add(d)
                    ded.append(d)
            rows.append(ded)
        return rows, time.time() - t

    rows_b, tb = rerank_variant(lambda c: c.text)
    rows_c, tc = rerank_variant(lambda c: c.text + "\n【所属】" + parent_first_sentence(clauses, c))

    print(f"\n{'变体':<26}{'池内含金标':>10}{'P@1':>9}{'P@1|池内':>11}{'MRR':>8}{'耗时':>9}")
    for name, rows, cost in (("A BM25 原序", base_orders, 0.0),
                             ("B 重排：条款", rows_b, tb),
                             ("C 重排：条款+父条首句", rows_c, tc)):
        m = metrics(rows, golds)
        print(f"{name:<24}{m['pool']:>11.1%}{m['p1']:>9.1%}{m['p1_in']:>11.1%}"
              f"{m['mrr']:>8.3f}{cost:>8.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
