"""引用保真度抽样：CLR 自动查，CSR 留给人标。

CLR（可定位）= 引用的证据编号真实存在（`verify.py` 已保证，这里只统计）。
CSR（能支撑）= 被引条款是否支撑那句话——法学界的通行做法是人工双标，报 precision + κ。

用法：
    python -m tools.eval_citation --sample 20        # 抽 20 题，生成 eval/citation_sample.csv
    python -m tools.eval_citation --score eval/citation_sample.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from app.answer import context as C
from app.answer.generate import generate
from app.answer.verify import check
from tools.evalkit import dataset as D

OUT = Path("eval/citation_sample.csv")
COLS = ["qid", "sentence", "doc_no", "page", "citation", "quote", "rater_a", "rater_b"]


def _kappa(a: list[str], b: list[str]) -> float:
    labels = sorted(set(a) | set(b))
    n = len(a)
    if not n:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return 0.0 if pe == 1 else (po - pe) / (1 - pe)


def sample(n: int) -> Path:
    rows: list[dict] = []
    for q in D.official("dev", limit=n):
        text = D.text_of(q)
        ev = C.evidence_for(text)
        if not ev:
            continue
        draft = generate(text, ev)
        if draft is None:
            continue
        chk = check(draft, ev)
        by = {e.eid: e for e in ev}
        for s in chk.kept:
            for cid in s.get("cites", []):
                e = by.get(cid)
                if e is None:
                    continue
                rows.append({
                    "qid": q["id"], "sentence": s.get("text", ""),
                    "doc_no": getattr(e, "doc_no", ""), "page": getattr(e, "page", ""),
                    "citation": getattr(e, "citation", ""), "quote": e.text[:160],
                    "rater_a": "", "rater_b": "",
                })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"写出 {len(rows)} 行 → {OUT}", file=sys.stderr)
    return OUT


def score(path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    labeled = [r for r in rows if r["rater_a"].strip().lower() in ("y", "n")]
    both = [r for r in labeled if r["rater_b"].strip().lower() in ("y", "n")]
    yes = sum(1 for r in labeled if r["rater_a"].strip().lower() == "y")
    print(f"CSR：已标 {len(labeled)}/{len(rows)} 行，支撑 {yes}/{len(labeled)}"
          f" = {yes / len(labeled):.0%}" if labeled else "CSR：还没有标注")
    if both:
        a = [r["rater_a"].strip().lower() for r in both]
        b = [r["rater_b"].strip().lower() for r in both]
        print(f"双标 {len(both)} 行，Cohen's κ = {_kappa(a, b):.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--score", default="")
    args = ap.parse_args(argv)
    if args.sample:
        sample(args.sample)
    if args.score:
        return score(Path(args.score))
    if not args.sample:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
