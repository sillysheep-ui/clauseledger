"""池子有多大影响：重排只能重排"召回给你的东西"。

问题：重排把 hit@1 从 54.7% 提到 64.2%，但候选只取 BM25 前 30 条。
      如果金标根本不在这 30 条里，重排再准也救不回来。

这一轮测三件事：
1. 各 K 下候选池的**上限**（金标在不在池里）——这是重排的天花板；
2. 换成"BM25 ∪ 稠密"的并集，上限能不能抬高；
3. 同样重排 40 条，用哪个池子更划算。

用法：
    python -m tools.experiments.rerank_pool
"""
from __future__ import annotations

import json
from pathlib import Path

from app.retrieval.records import Clause
from app.retrieval.search import Hit, _collapse, _prefer_effective
from tools.experiments.recall_compare import (
    bm25_rank,
    build_bm25,
    dense_matrix,
    dense_rank,
    load_corpus,
    to_pids,
)

REPORTS = Path("eval/reports")
KS = (10, 20, 30, 50, 100)


def ceiling(pools: list[list[int]], clauses: list[Clause], qs: list[dict]) -> dict[int, int]:
    """每个 K 下，金标落在池子里的题数。"""
    out = {k: 0 for k in KS}
    for pool, q in zip(pools, qs):
        hits = _prefer_effective(_collapse([Hit(clauses[p], 0.0) for p in pool]))
        gold_rank = 0
        for i, h in enumerate(hits, 1):
            if h.clause.doc_no == q["gold_doc"] or (q["gold_title"] and h.clause.title == q["gold_title"]):
                gold_rank = i
                break
        if gold_rank:
            for k in KS:
                if gold_rank <= k:
                    out[k] += 1
    return out


def main() -> None:
    clauses, children, qs = load_corpus()
    print(f"语料 {len(clauses)} 条款 / 题 {len(qs)} 条", flush=True)

    bm, _ = build_bm25([c["text"] for c in children], False)
    mat, _ = dense_matrix(children)

    pools: dict[str, list[list[int]]] = {"bm25": [], "dense": [], "union": []}
    for q in qs:
        b = [p for p, _ in to_pids(bm25_rank(bm, children, q["q"], False), children)][:100]
        d = [p for p, _ in to_pids(dense_rank(mat, children, q["q"]), children)][:100]
        u, seen = [], set()
        for p in b[:50] + d[:50]:
            if p not in seen:
                seen.add(p)
                u.append(p)
        pools["bm25"].append(b)
        pools["dense"].append(d)
        pools["union"].append(u)

    print(f"\n候选池上限（金标在池内的题数 / 均为 {len(qs)}）")
    print("| K | BM25 | 稠密 | 并集 |")
    print("|---|---|---|---|")
    for k in KS:
        row = [ceiling([p[:k] for p in pools[name]], clauses, qs)[k] for name in ("bm25", "dense", "union")]
        print(f"| {k} | {row[0]} ({row[0]/len(qs):.0%}) | {row[1]} ({row[1]/len(qs):.0%}) | {row[2]} ({row[2]/len(qs):.0%}) |")

    # 并集去重后大小
    sizes = [len(p) for p in pools["union"]]
    print(f"\n并集（各取 50）平均大小 {sum(sizes)/len(sizes):.1f} 条")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = {"n": len(qs), "ceiling": {name: ceiling(pools[name], clauses, qs) for name in pools}}
    (REPORTS / "rerank_pool.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"→ {REPORTS/'rerank_pool.json'}")


if __name__ == "__main__":
    main()
