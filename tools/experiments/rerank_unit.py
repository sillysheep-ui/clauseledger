"""重排单位：整条条款 vs 子块。

问题：cross-encoder 只吃 512 token（约 700–900 汉字）。一条 17,788 字的条款
不可能整个喂进去，只能截断——截掉的部分等于没看。

两条路：
1. **截断整条**（现在线上用的）：`text[:1200]`，简单，但长条款只看开头；
2. **按子块打分取最大**：我们本来就有 children（≤500 字），逐块打分取 max，
   覆盖全文且每块都在窗口内。

这份脚本在同一批题上跑两种，看差别有多大。

用法：
    python -m tools.experiments.rerank_unit
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sentence_transformers import CrossEncoder

from app.retrieval.records import Clause
from app.retrieval.search import Hit, _collapse, _prefer_effective

from tools.experiments.recall_compare import (
    bm25_rank, build_bm25, load_corpus, mcnemar, to_pids,
)

REPORTS = Path("eval/reports")
MODEL = "BAAI/bge-reranker-base"
CANDS = 30


def rank_clause(hits: list[Hit], q: dict) -> int:
    from tools.eval_v2 import is_hit
    for i, h in enumerate(hits, 1):
        if is_hit(h, q["gold_doc"], q["gold_title"]):
            return i
    return 0


def score_by_parent(model, q: str, cands: list[Clause]) -> list[float]:
    pairs = [(q, c.text[:1200]) for c in cands]
    return [float(x) for x in model.predict(pairs, batch_size=16, show_progress_bar=False)]


def score_by_child(model, q: str, cands: list[Clause], kids: dict[int, list[str]]) -> list[float]:
    pairs, owner = [], []
    for i, c in enumerate(cands):
        for t in kids.get(c.id, [c.text]):
            pairs.append((q, t[:1200]))
            owner.append(i)
    if not pairs:
        return [0.0] * len(cands)
    sc = [float(x) for x in model.predict(pairs, batch_size=32, show_progress_bar=False)]
    best = [0.0] * len(cands)
    for i, s in zip(owner, sc):
        if s > best[i]:
            best[i] = s
    return best


def main() -> None:
    clauses, children, qs = load_corpus()
    kids: dict[int, list[str]] = {}
    for ch in children:
        kids.setdefault(int(ch["pid"]), []).append(ch["text"])
    print(f"语料 {len(clauses)} 条款 / {len(children)} 子块 / 题 {len(qs)} 条", flush=True)

    bm, _ = build_bm25([c["text"] for c in children], False)
    model = CrossEncoder(MODEL, max_length=512)

    rows = {"parent": [], "child": []}
    lat = {"parent": [], "child": []}
    truncated = 0
    for q in qs:
        pids = [p for p, _ in to_pids(bm25_rank(bm, children, q["q"], False), children)][:CANDS]
        cands = [clauses[p] for p in pids]
        truncated += sum(1 for c in cands if len(c.text) > 1200)

        t0 = time.perf_counter()
        sp = score_by_parent(model, q["q"], cands)
        lat["parent"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        sk = score_by_child(model, q["q"], cands, kids)
        lat["child"].append(time.perf_counter() - t0)

        for name, scores in (("parent", sp), ("child", sk)):
            ordered = [h for _, h in sorted(zip(scores, [Hit(c, s) for c, s in zip(cands, scores)]),
                                            key=lambda x: -x[0])]
            hits = _prefer_effective(_collapse(ordered))
            rows[name].append({"id": q["id"], "rank": rank_clause(hits, q)})

    def summarize(name: str) -> dict:
        rs = rows[name]
        n = len(rs)
        h1 = sum(1 for r in rs if r["rank"] == 1)
        h3 = sum(1 for r in rs if 1 <= r["rank"] <= 3)
        mrr = sum(1 / r["rank"] if r["rank"] else 0.0 for r in rs) / n
        l = sorted(lat[name])[len(rs) // 2] * 1000
        return {"n": n, "hit1": h1, "hit1_pct": h1 / n, "hit3": h3, "mrr": mrr,
                "med_ms": l, "rows": rs}

    a, b = summarize("parent"), summarize("child")
    print(f"\n候选里被截断（>1200 字）的条款：{truncated} 条次 / {len(qs)*CANDS} 条次")
    print("\n| 重排单位 | hit@1 | hit@3 | MRR | 中位延迟 |")
    print("|---|---|---|---|---|")
    for name, s in (("整条（截断 1200 字）", a), ("子块取最大", b)):
        print(f"| {name} | {s['hit1']}/{s['n']} = {s['hit1_pct']:.1%} | {s['hit3']} | {s['mrr']:.3f} | {s['med_ms']:.0f} ms |")
    m = mcnemar(a, b)
    print(f"\n配对（子块 vs 整条）：改好 {m['better']} / 改坏 {m['worse']}，p={m['p']:.3f}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "rerank_unit.json").write_text(
        json.dumps({"parent": a, "child": b, "mcnemar": m}, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"→ {REPORTS/'rerank_unit.json'}")


if __name__ == "__main__":
    main()
