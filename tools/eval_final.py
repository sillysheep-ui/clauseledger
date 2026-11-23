"""定稿数字：在 dev / test / holdout 三个 split 上跑同一套口径。

口径来自 `docs/EVAL-SPEC.md`：
  * 官方题、`q_ctx`、排除 ambiguous/stale、金标严格按文号；
  * L1/L2 放开阈值只看候选，L5 按生产阈值只看"敢不敢答、答得对不对"；
  * 比例给 Wilson 区间——n=26 的 holdout 上，1 道题的差别本来就不该被当成结论。

    python -m tools.eval_final            # 跑三个 split，写 docs/EVAL-FINAL.md
    python -m tools.eval_final --split test
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.eval_layers import decide, load, neg_refuse, surface
from tools.eval_v2 import wilson

QUESTIONS = Path("eval/questions_v3.json")
OUT = Path("docs/EVAL-FINAL.md")
REPORTS = Path("eval/reports")


def pct(pair: tuple[int, int]) -> str:
    got, n = pair
    if not n:
        return "—"
    lo, hi = wilson(got, n)
    return f"{got}/{n} = {got / n:.0%} ({lo:.0%}–{hi:.0%})"


def one(split: str) -> dict:
    rows = load(QUESTIONS, ("official",), (split,))
    s = surface(rows)
    d = decide(rows)
    oos = load(QUESTIONS, ("out_of_scope",), (split,))
    o = neg_refuse(oos) if oos else {"refused": 0, "n": 0, "wrong": []}
    return {"split": split, "n": len(rows), "surface": s, "decide": d, "oos": o}


def main() -> None:
    results = [one(s) for s in ("dev", "test", "holdout")]
    lines = ["# 定稿评测（三个 split 同一口径）", "",
             "口径见 `docs/EVAL-SPEC.md`：官方题、`q_ctx`、排除 ambiguous/stale、"
             "金标严格按文号；区间为 Wilson 95%。", "",
             "| split | n | R@5 | R@10 | P@1\\|R@10 | 作答 | 作答精度 | 同族错版 | 域外正确拒答 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s, d = r["surface"], r["decide"]
        lines.append(
            f"| {r['split']} | {r['n']} | {pct(s['R@5'])} | {pct(s['R@10'])} | "
            f"{pct(s['P@1|R@10'])} | {d['answered']}/{d['n']} = {d['answered'] / max(1, d['n']):.0%} | "
            f"{d['correct']}/{max(1, d['answered'])} = {d['correct'] / max(1, d['answered']):.0%} | "
            f"{s['family_wrong']} | {pct((r['oos']['refused'], r['oos']['n']))} |")
    lines += ["", "## 怎么读", "",
              "- **dev 是唯一允许用来调参的 split**；test/holdout 只在定稿时跑，跑完不改。",
              "- holdout 只有 30 题：区间宽，只用来发现离谱，不用来比较 1–3 个百分点的差别。",
              "- `P@1|R@10` 的分母是候选池里确实有金标的题，衡量排序而不是召回。",
              "- 同族错版：标题同、文号不同（旧版本）——它被算作答错，但单独计数。"]
    OUT.write_text("\n".join(lines) + "\n", "utf-8")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "final.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(json.dumps(results, ensure_ascii=False, default=str)[:400])


if __name__ == "__main__":
    main()
