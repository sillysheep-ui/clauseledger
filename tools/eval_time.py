"""时间类评测：同一个问题，年份不同，答案必须指向不同文件。

为什么单独一套：常规题集里没有一条带年份，所以"时间感知"这个能力**测不到**。
这里用一对一对的题（同主题、不同年份），只看三件事：

1. 单题 hit@1 —— 那一年该用的文件排第一了吗
2. 成对通过 —— 两题都命中才算这对成立（只有一题对说明它是靠别的线索蒙的）
3. 同一条 —— 两题返回的文件是否相同；相同就说明系统没看年份（越少越好）

用法：python -m tools.eval_time [--write]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.eval.metrics import wilson_interval as wilson
from app.retrieval.search import search_result

QS = Path("eval/questions_time.json")
REPORT_DIR = Path("eval/reports")


def is_hit(hit, gold: str, title: str = "") -> bool:
    return hit.clause.doc_no == gold or (title and hit.clause.title == title)


def run() -> dict:
    qs = json.loads(QS.read_text("utf-8"))
    rows = []
    for q in qs:
        a = search_result(q["q"], k=10)
        top = a.hits[0] if a.hits else None
        rows.append({
            "id": q["id"], "pair": q["pair"], "year": q["year"], "q": q["q"],
            "gold": q["gold_doc"],
            "top": top.clause.doc_no if top else None,
            "ok": bool(top and is_hit(top, q["gold_doc"], q.get("gold_title"))),
            "as_of": a.as_of,
        })
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[r["pair"]].append(r)
    pairs = []
    for pid, rs in sorted(by_pair.items()):
        ok = all(r["ok"] for r in rs)
        same = len({r["top"] for r in rs}) == 1
        pairs.append({"pair": pid, "ok": ok, "same_doc": same,
                      "tops": [r["top"] for r in rs], "gold": [r["gold"] for r in rs]})
    return {"rows": rows, "pairs": pairs}


def report(res: dict) -> str:
    rows, pairs = res["rows"], res["pairs"]
    n = len(rows)
    k = sum(1 for r in rows if r["ok"])
    lo, hi = wilson(k, n)
    npair = len(pairs)
    kpair = sum(1 for p in pairs if p["ok"])
    plo, phi = wilson(kpair, npair)
    same = sum(1 for p in pairs if p["same_doc"])
    L = ["# 时间类评测（answer-flipping）", "",
         f"- 题目：**{n}** 条（{npair} 对，同主题不同年份）",
         f"- 单题 hit@1：**{k}/{n} = {k/n:.0%}**（{lo:.0%}–{hi:.0%}）",
         f"- 成对通过：**{kpair}/{npair} = {kpair/npair:.0%}**（{plo:.0%}–{phi:.0%}）",
         f"- 两题返回同一份文件：**{same}/{npair}**（越低说明越看年份）", "",
         "| 对 | 年份 | 金标 | 实际 top-1 | 命中 |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['pair']} | {r['year']} | {r['gold']} | {r['top'] or '拒答'} | "
                 f"{'✓' if r['ok'] else '✗'} |")
    bad = [p for p in pairs if not p["ok"]]
    if bad:
        L += ["", "## 未通过的对", ""]
        for p in bad:
            L.append(f"- {p['pair']}：金标 {p['gold']}，实际 {p['tops']}"
                     f"{'（两题相同 = 没看年份）' if p['same_doc'] else ''}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    res = run()
    md = report(res)
    print(md)
    if a.write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        (REPORT_DIR / f"{ts}-time.md").write_text(md, "utf-8")
        print(f"\n→ eval/reports/{ts}-time.md")


if __name__ == "__main__":
    main()
