"""评测运行器：smoke / full，带缓存与并发，落报告。

用法：
    python -m tools.evalkit.run --suite smoke
    python -m tools.evalkit.run --suite full --split dev
    python -m tools.evalkit.run --suite full --debug-rank --no-cache
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from tools.evalkit import dataset as D
from tools.evalkit import graders as G
from tools.evalkit import report as R

OUT = Path("eval/reports")
CACHE = Path("eval/cache")


def _corpus_hash() -> str:
    p = Path("data/index/stats.json")
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except FileNotFoundError:
        return "nocorpus"


def _cfg() -> str:
    return "v2-rr" + os.environ.get("CLAUSELEDGER_RERANK", "1") + "-" + _corpus_hash()


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _one(q: dict, answers: dict[str, str]) -> dict:
    ans = answers.get(q["id"], "")
    hits = G.S.ranked_clauses(q.get("q_ctx") or q["q"], k=G.RANK_K)
    rank, state = G.clause_rank(q, ans, hits=hits)
    aec = G.answer_evidence_coverage(q, ans, hits=hits)
    ref = G.refusal(q)
    tax = ("stale_gold" if state == "stale_gold"
           else ("ok" if (aec["hit"] and rank) else
                 ("retrieval_miss" if rank is None else "sentence_missed")))
    return {"id": q["id"], "split": q.get("split"), "rank": rank, "state": state,
            "aec": aec["hit"], "items": aec["items"], "chars": aec["chars"],
            "outcome": ref["outcome"], "taxonomy": tax}


def evaluate(rows: list[dict], answers: dict[str, str], workers: int, use_cache: bool) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cp = CACHE / f"{_cfg()}.json"
    cache = _load_cache(cp) if use_cache else {}
    todo = [q for q in rows if q["id"] not in cache]
    if todo:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(lambda q: _one(q, answers), todo):
                cache[r["id"]] = r
        print(f"  算了 {len(todo)} 题（{time.time()-t0:.0f}s，{workers} 线程）", file=sys.stderr)
        if use_cache:
            cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return [cache[q["id"]] for q in rows]


def negatives(split: str | None) -> dict:
    np_rows = D.in_domain_no_policy(split)
    oos = D.out_of_scope(split)
    np_ref = sum(1 for q in np_rows if G.refusal(q)["outcome"] != "answer")
    oos_ref = sum(1 for q in oos if G.refusal(q)["outcome"] != "answer")
    neg_ans = sum(1 for q in np_rows + oos if G.refusal(q)["outcome"] == "answer")
    return {"no_policy_n": len(np_rows), "no_policy_refused": np_ref,
            "oos_n": len(oos), "oos_refused": oos_ref, "neg_answered": neg_ans}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    ap.add_argument("--split", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--debug-rank", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    answers = D.answers()
    splits = [args.split] if args.split else (["dev"] if args.suite == "smoke" else ["dev", "test", "holdout"])
    rows: list[dict] = []
    for sp in splits:
        limit = args.limit or (10 if args.suite == "smoke" else 0)
        partial = D.official(sp, limit=limit or None)
        print(f"[{sp}] {len(partial)} 题", file=sys.stderr)
        rows += evaluate(partial, answers, args.workers, not args.no_cache)

    neg = negatives(splits[0] if len(splits) == 1 else None)
    false_refused = sum(1 for r in rows if r["outcome"] != "answer")
    res = {"suite": args.suite, "splits": splits, "rows": rows, "negatives": neg,
           "false_refused": false_refused, "rerank": os.environ.get("CLAUSELEDGER_RERANK", "1"),
           "corpus": R.corpus_version()}
    if args.debug_rank:
        n = len(rows)
        res["rank_debug"] = {"answered": f"{n - false_refused}/{n}",
                             "note": "doc 级 rank 已退役，只在这里打印条数"}

    md = R.render(f"评测报告 · {args.suite}", res)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT.mkdir(parents=True, exist_ok=True)
    path = Path(args.out) if args.out else OUT / f"eval-{stamp}-{args.suite}.md"
    path.write_text(md, encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md)
    print(f"\n写到 {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
