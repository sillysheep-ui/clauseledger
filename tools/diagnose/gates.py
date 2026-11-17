"""误拒诊断：把每一条被拒答的题摊开，看它死在哪一道门、金标还在不在候选里。

三道前置闸（城市 / 领域 / 部门）在检索前就把问题挡掉了，`searched` 里的
n_queries 是 0；后两道（候选过滤、覆盖率）发生在检索后，能看到候选与覆盖率。
这个脚本两种都记，并额外跑一次"放开阈值"的检索，用来看金标本来排第几。

用法：
  python -m tools.diagnose.gates                # dev 官方题
  python -m tools.diagnose.gates --write        # 同时落盘 data/reports/gates.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.retrieval.search import refuse_reason, search_result
from tools.eval_v2 import is_hit

QS = Path("eval/questions_v2.json")
OUT = Path("data/reports/gates.md")


def gold_rank(hits, q) -> int:
    for i, h in enumerate(hits, 1):
        if is_hit(h, q["gold_doc"], q["gold_title"]):
            return i
    return 0


def classify(reason: str, outcome: str) -> str:
    if reason:
        if reason.startswith(("未覆盖", "未接入")):
            return "A 城市/部门闸"
        return "B 领域闸"
    if outcome == "insufficient":
        return "C 覆盖率闸"
    return "OK"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q.get("split") == "dev" and q.get("category") == "official"
          and not q.get("ambiguous") and not q.get("stale")]

    rows = []
    for q in qs:
        ans = search_result(q["q"], k=10)
        pre = refuse_reason(q["q"])
        # 放开两道检索后闸门，看金标本来排第几、覆盖率多少
        loose = search_result(q["q"], k=10, min_coverage=0.0, min_cand_cov=0.0)
        gr = gold_rank(loose.hits, q)
        cov = loose.hits[0].coverage if loose.hits else 0.0
        rows.append({
            "id": q["id"], "q": q["q"],
            "outcome": ans.outcome, "reason": ans.reason,
            "gate": classify(pre, ans.outcome),
            "n_cands": len(loose.hits), "gold_rank": gr,
            "top1_cov": cov,
        })

    refused = [r for r in rows if r["outcome"] != "ok"]
    ok = [r for r in rows if r["outcome"] == "ok"]
    h1 = sum(1 for r in ok if r["gold_rank"] == 1)

    print(f"dev 官方 {len(rows)} 条：答 {len(ok)} / 拒 {len(refused)}；"
          f"已答中 hit@1 {h1}/{len(ok)} = {100*h1/max(1,len(ok)):.0f}%")
    print(f"拒绝里按闸分类：" + ", ".join(
        f"{k} {sum(1 for r in refused if r['gate']==k)}"
        for k in sorted({r["gate"] for r in refused})))
    print()
    for r in sorted(refused, key=lambda x: (x["gate"], x["id"])):
        print(f"{r['id']} [{r['gate']}] rank={r['gold_rank']} cov={r['top1_cov']:.2f} "
              f"cands={r['n_cands']}  {r['q'][:38]}")
        print(f"    闸门原话：{r['reason'][:70]}")

    if a.write:
        lines = ["# 误拒诊断", "",
                 f"dev 官方 {len(rows)} 条：答 {len(ok)} / 拒 {len(refused)}。", "",
                 "| id | 闸 | 金标名次 | top1 覆盖 | 候选数 | 问题 |",
                 "|---|---|---|---|---|---|"]
        for r in sorted(refused, key=lambda x: x["id"]):
            lines.append(f"| {r['id']} | {r['gate']} | {r['gold_rank']} | "
                         f"{r['top1_cov']:.2f} | {r['n_cands']} | {r['q'][:40]} |")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines) + "\n", "utf-8")
        print("\n→", OUT)


if __name__ == "__main__":
    main()
