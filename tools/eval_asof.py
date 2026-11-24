"""A 口径：以解读文章发布日为 as_of，测「当时能不能找对文件」。

和默认口径的区别只有两处：
  1. 检索带 `as_of`——那天不在有效期的文件不进候选，同族优先「当时有效」的版本；
  2. 分母只含金标在 as_of 当天有效的题（`stale_asof == False`）。

金标在 as_of 已过期 / 未生效的题不是"我们答错"，而是题目前提与语料对不上，
单独报数，不混进分母——混进去等于用 2023 年的文章考 2026 年的政策库。

用法：
  python -m tools.eval_asof --write   # 写 docs/EVAL-ASOF.md 与 eval/reports/asof.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tools.eval_v2 import norm_title, wilson

QS = "eval/questions_v3.json"
ASOF = Path("data/eval/asof.json")
OUT = Path("docs/EVAL-ASOF.md")
REPORT = Path("eval/reports/asof.json")


def load() -> tuple[list[dict], dict[str, dict]]:
    qs = [q for q in json.loads(Path(QS).read_text("utf-8"))
          if q.get("category") == "official" and not q.get("ambiguous") and not q.get("stale")]
    asof = json.loads(ASOF.read_text("utf-8"))
    return qs, asof


def run_split(rows: list[dict], asof: dict[str, dict], use_asof: bool) -> dict:
    """use_asof=False 时同一批题按今天跑，用来对比。"""
    from app.retrieval.search import search_result as S

    res: list[dict] = []
    t0 = time.time()
    for q in rows:
        meta = asof[q["id"]]
        kw = {"as_of": meta["as_of"]} if use_asof else {}
        ans = S(q.get("q_ctx") or q["q"], k=10, **kw)
        rank = None
        for i, h in enumerate(ans.hits, 1):
            if h.clause.doc_no == q["gold_doc"]:
                rank = i
                break
        res.append({"id": q["id"], "rank": rank, "outcome": ans.outcome,
                    "top1": ans.hits[0].clause.doc_no if ans.hits else None,
                    "fam_wrong": bool(rank is None and ans.hits and
                                      norm_title(ans.hits[0].clause.title)
                                      == norm_title(q.get("gold_title") or ""))})
    n = len(res)
    out = {"n": n, "seconds": round(time.time() - t0, 1)}
    for k in (1, 3, 5, 10):
        got = sum(1 for r in res if r["rank"] and r["rank"] <= k)
        out[f"R@{k}"] = (got, n)
    pool = [r for r in res if r["rank"] and r["rank"] <= 10]
    out["P@1|R@10"] = (sum(1 for r in pool if r["rank"] == 1), len(pool))
    ans_rows = [r for r in res if r["outcome"] == "answer"]
    out["answered"] = (len(ans_rows), n)
    out["correct"] = (sum(1 for r in ans_rows if r["rank"] == 1), len(ans_rows))
    out["family_wrong"] = sum(1 for r in res if r["fam_wrong"])
    out["rows"] = res
    return out


def fmt(out: dict) -> str:
    def w(key):
        k, n = out[key]
        lo, hi = wilson(k, n)
        return f"{k}/{n} = {k/n:.0%} ({lo:.0%}–{hi:.0%})" if n else "—"
    return (f"R@1 {w('R@1')} · R@3 {w('R@3')} · R@5 {w('R@5')} · R@10 {w('R@10')} · "
            f"P@1|R@10 {w('P@1|R@10')} · 作答 {w('answered')} · 精度 {w('correct')} · "
            f"同族错版 {out['family_wrong']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    qs, asof = load()
    ok = [q for q in qs if not asof[q["id"]]["stale_asof"]]
    stale = [q for q in qs if asof[q["id"]]["stale_asof"]]
    lines = ["# A 口径：按文章发布日的 as_of 测", ""]
    print(f"可测 {len(ok)} 道，前提已破 {len(stale)} 道")
    payload: dict = {"as_of": {}, "today": {}, "stale": len(stale)}
    for split in ("dev", "test", "holdout"):
        rows = [q for q in ok if q.get("split") == split]
        if not rows:
            continue
        a = run_split(rows, asof, use_asof=True)
        b = run_split(rows, asof, use_asof=False)
        payload["as_of"][split] = {k: v for k, v in a.items() if k != "rows"}
        payload["today"][split] = {k: v for k, v in b.items() if k != "rows"}
        print(f"[{split}] as_of: {fmt(a)}")
        print(f"[{split}] today: {fmt(b)}")
        lines += [f"## {split}（n={a['n']}，{a['seconds']}s）", "",
                  "| 口径 | 指标 |", "|---|---|",
                  f"| A（as_of） | {fmt(a)} |", f"| 今天 | {fmt(b)} |", ""]
    lines += [f"前提已破（金标在 as_of 已过期/未生效）：**{len(stale)}/{len(qs)}** 道，不进 A 分母。", ""]
    if args.write:
        OUT.write_text("\n".join(lines), "utf-8")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
        print(f"写入 {OUT} / {REPORT}")


if __name__ == "__main__":
    main()
