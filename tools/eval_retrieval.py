"""检索层评测：50 条冻结用例，只测机械可判定的东西。

四类用例：
  literal      用政策里的词问（应该找得到）
  paraphrase   用口语问，不含标题里的词（现在大概率找不到，这是要填的坑）
  version      同名文件有多个版本（应该答现行那版）
  out_of_scope 库里没有的主题 / 没接入的城市（应该拒答，而不是编）

指标：
  hit@1 / hit@3   第一条/前三条里有没有切题的文件（按标题关键词判）
  MRR@10          正确文件排名的倒数均值
  refusal_rate    out_of_scope 里正确拒答的比例（目标 100%）
  false_answer    out_of_scope 里给出了条款的比例（目标 0，最危险的一格）
  effective@1     version 类里第一条是"现行"的比例

用法：python -m tools.eval_retrieval [--write]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from app.retrieval.search import search

QUESTIONS = Path("eval/questions.json")
REPORTS = Path("eval/reports")
CATS = ("literal", "paraphrase", "version", "out_of_scope")


def rank_of(hits, kws) -> int:
    for i, h in enumerate(hits, 1):
        if any(k in h.clause.title for k in kws):
            return i
    return 0


def run() -> dict:
    qs = json.loads(QUESTIONS.read_text("utf-8"))
    per = {c: {"n": 0, "hit1": 0, "hit3": 0, "mrr": 0.0, "cov": 0.0,
               "refused": 0, "answered": 0, "effective1": 0} for c in CATS}
    rows = []
    for item in qs:
        cat, q = item["category"], item["q"]
        hits = search(q, k=10)
        b = per[cat]
        b["n"] += 1
        if cat == "out_of_scope":
            if not hits:
                b["refused"] += 1
            else:
                b["answered"] += 1
            rows.append({"q": q, "cat": cat, "top": hits[0].clause.doc_no if hits else "",
                         "ok": not hits})
            continue
        r = rank_of(hits, item["expect_title_kw"])
        b["hit1"] += r == 1
        b["hit3"] += 1 <= r <= 3
        b["mrr"] += 1 / r if r else 0.0
        b["cov"] += hits[0].coverage if hits else 0.0
        if cat == "version" and hits:
            b["effective1"] += hits[0].clause.status == "effective"
        rows.append({"q": q, "cat": cat, "rank": r,
                     "top": hits[0].clause.doc_no if hits else "",
                     "status": hits[0].clause.status if hits else "",
                     "cov": round(hits[0].coverage, 2) if hits else 0.0})
    return {"per": per, "rows": rows}


def report(res: dict) -> str:
    L = ["# 检索评测报告", "", f"用例 {len(res['rows'])} 条（`eval/questions.json`，冻结）", "",
         "| 类别 | n | hit@1 | hit@3 | MRR@10 | 首条平均覆盖率 | 拒答率 | 误答 | 首条现行 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for c in CATS:
        b = res["per"][c]
        n = b["n"] or 1
        if c == "out_of_scope":
            L.append(f"| {c} | {b['n']} | — | — | — | — | "
                     f"{b['refused']/n:.0%} | {b['answered']} | — |")
        else:
            eff = f"{b['effective1']/n:.0%}" if c == "version" else "—"
            L.append(f"| {c} | {b['n']} | {b['hit1']/n:.0%} | {b['hit3']/n:.0%} | "
                     f"{b['mrr']/n:.2f} | {b['cov']/n:.0%} | — | — | {eff} |")
    miss = [r for r in res["rows"] if r["cat"] != "out_of_scope" and not r.get("rank")]
    wrong = [r for r in res["rows"] if r["cat"] == "out_of_scope" and r["top"]]
    L += ["", f"## 没命中（{len(miss)} 条）", ""]
    L += [f"- [{r['cat']}] {r['q']} → {r['top'] or '拒答'}" for r in miss]
    L += ["", f"## 该拒答却给了条款（{len(wrong)} 条）", ""]
    L += [f"- {r['q']} → {r['top']}" for r in wrong]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    res = run()
    md = report(res)
    print(md)
    print(f"\n耗时 {time.time()-t0:.1f}s")
    if a.write:
        REPORTS.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
        except OSError:
            commit = "?"
        meta = {"ts": ts, "commit": commit,
                "index": str(Path("data/index/stats.json")),
                "index_bytes": Path("data/index/bm25").stat().st_size if Path("data/index/bm25").exists() else 0}
        (REPORTS / f"{ts}.md").write_text(md, "utf-8")
        (REPORTS / f"{ts}.json").write_text(json.dumps({"meta": meta, **res}, ensure_ascii=False, indent=1), "utf-8")
        print(f"已写入 eval/reports/{ts}.md")


if __name__ == "__main__":
    main()
