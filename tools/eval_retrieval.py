"""检索层评测：冻结用例 + 95% 区间 + 按 dev/test 分开看。

设计约束（见 eval/FROZEN.json）：
- dev 用来调，test 只在发版前跑一次，不参与任何决策
- 指标一律报 Wilson 区间，不报裸点估计
- 每题结果落盘，供 eval_compare 做配对比较

用法：
    python -m tools.eval_retrieval --split dev --write
    python -m tools.eval_retrieval --split test --write
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from app.retrieval.search import refuse_reason, search

QUESTIONS = Path("eval/questions.json")
REPORTS = Path("eval/reports")
CATS = ("literal", "paraphrase", "version", "out_of_scope")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def rank_of(hits, kws) -> int:
    for i, h in enumerate(hits, 1):
        if any(k in h.clause.title for k in kws):
            return i
    return 0


def run(split: str) -> dict:
    qs = [q for q in json.loads(QUESTIONS.read_text("utf-8"))
          if split == "all" or q["split"] == split]
    rows = []
    for item in qs:
        cat, q = item["category"], item["q"]
        hits = search(q, k=10)
        row = {"id": item["id"], "q": q, "cat": cat, "split": item["split"],
               "top": hits[0].clause.doc_no if hits else "",
               "top_title": hits[0].clause.title if hits else "",
               "status": hits[0].clause.status if hits else "",
               "cov": round(hits[0].coverage, 2) if hits else 0.0,
               "reason": refuse_reason(q) or ""}
        if cat == "out_of_scope":
            row.update(rank=0, ok1=int(not hits), ok3=int(not hits), mrr=0.0,
                       answered=int(bool(hits)))
        else:
            r = rank_of(hits, item["expect_title_kw"])
            row.update(rank=r, ok1=int(r == 1), ok3=int(1 <= r <= 3),
                       mrr=(1 / r if r else 0.0), answered=int(bool(hits)))
        rows.append(row)
    return {"split": split, "rows": rows}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for cat in CATS:
        rs = [r for r in rows if r["cat"] == cat]
        n = len(rs)
        if not n:
            continue
        if cat == "out_of_scope":
            ok = sum(r["ok1"] for r in rs)
            lo, hi = wilson(ok, n)
            out[cat] = {"n": n, "refused": ok, "refusal_rate": ok / n,
                        "ci": [lo, hi], "answered": sum(r["answered"] for r in rs)}
        else:
            h1, h3 = sum(r["ok1"] for r in rs), sum(r["ok3"] for r in rs)
            l1, u1 = wilson(h1, n)
            l3, u3 = wilson(h3, n)
            eff = sum(1 for r in rs if r["status"] == "effective")
            out[cat] = {"n": n, "hit1": h1 / n, "hit1_ci": [l1, u1],
                        "hit3": h3 / n, "hit3_ci": [l3, u3],
                        "mrr": sum(r["mrr"] for r in rs) / n,
                        "effective1": eff / n}
    return out


def report(res: dict) -> str:
    s = summarize(res["rows"])
    L = [f"# 检索评测（{res['split']}，{len(res['rows'])} 条）", "",
         "| 类别 | n | hit@1 (95%CI) | hit@3 (95%CI) | MRR | 拒答率 (95%CI) | 误答 | 首条现行 |",
         "|---|---|---|---|---|---|---|---|"]
    for cat in CATS:
        b = s.get(cat)
        if not b:
            continue
        if cat == "out_of_scope":
            lo, hi = b["ci"]
            L.append(f"| {cat} | {b['n']} | — | — | — | {b['refusal_rate']:.0%} ({lo:.0%}–{hi:.0%}) | {b['answered']} | — |")
        else:
            l1, u1 = b["hit1_ci"]; l3, u3 = b["hit3_ci"]
            eff = f"{b['effective1']:.0%}" if cat == "version" else "—"
            L.append(f"| {cat} | {b['n']} | {b['hit1']:.0%} ({l1:.0%}–{u1:.0%}) | "
                     f"{b['hit3']:.0%} ({l3:.0%}–{u3:.0%}) | {b['mrr']:.2f} | — | — | {eff} |")
    miss = [r for r in res["rows"] if r["cat"] != "out_of_scope" and not r["rank"]]
    wrong = [r for r in res["rows"] if r["cat"] == "out_of_scope" and r["top"]]
    if miss:
        L += ["", f"## 没命中（{len(miss)}）", ""] + [f"- [{r['cat']}] {r['q']} → {r['top'] or '拒答'}" for r in miss]
    if wrong:
        L += ["", f"## 该拒答却给了条款（{len(wrong)}）", ""] + [f"- {r['q']} → {r['top']}" for r in wrong]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=("dev", "test", "all"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    res = run(a.split)
    md = report(res)
    print(md)
    print(f"\n{len(res['rows'])} 条 / {time.time()-t0:.1f}s")
    if a.write:
        REPORTS.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        frozen = json.loads(Path("eval/FROZEN.json").read_text("utf-8"))
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
        except OSError:
            commit = "?"
        meta = {"ts": ts, "commit": commit, "eval_version": frozen["version"],
                "eval_sha256": frozen["sha256"], "split": a.split}
        (REPORTS / f"{ts}-{a.split}.md").write_text(md, "utf-8")
        (REPORTS / f"{ts}-{a.split}.json").write_text(
            json.dumps({"meta": meta, "summary": summarize(res["rows"]), "rows": res["rows"]},
                       ensure_ascii=False, indent=1), "utf-8")
        print(f"→ eval/reports/{ts}-{a.split}.md")


if __name__ == "__main__":
    main()
