"""在域内、但语料确实没有的题：正确的结局是拒答。

这批题的价值只有一个：**证明覆盖率闸门选不出"没有"**。
它们跟 out_of_scope 不同——领域闸往往拦不住（问的就是人社的事），
所以能真正考验"敢不敢说没有"。

用法：python -m tools.eval_missing [--out data/reports/missing.md]
"""
from __future__ import annotations

import json
from pathlib import Path

from app.retrieval.search import search_result

QS = Path("eval/questions_missing.json")


def run(min_answer: float = 0.30) -> list[dict]:
    rows = []
    for q in json.loads(QS.read_text("utf-8")):
        a = search_result(q["q"], k=3, min_answer=min_answer)
        top = a.hits[0] if a.hits else None
        rows.append({
            "id": q["id"], "q": q["q"], "note": q.get("note", ""),
            "outcome": a.outcome, "confidence": a.confidence,
            "top": top.clause.doc_no if top else "",
            "top_title": top.clause.title if top else "",
            "cov": round(top.coverage, 2) if top else 0.0,
            "score": round(top.score, 2) if top else 0.0,
            "margin": round(top.score - a.hits[1].score, 2) if len(a.hits) > 1 else 0.0,
        })
    return rows


def report(rows: list[dict]) -> str:
    refused = [r for r in rows if r["outcome"] != "answer"]
    bad = [r for r in rows if r["outcome"] == "answer"]
    L = [f"# 在域内但没有政策：{len(rows)} 条", "",
         f"- 正确拒答：**{len(refused)}/{len(rows)}**",
         f"- 错误作答：**{len(bad)}/{len(rows)}**", "",
         "| 题 | 结局 | 覆盖率 | 首条分 | 与第二名的差 | 引到的文件 |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['q'][:22]} | {r['outcome']}/{r['confidence']} | {r['cov']:.2f} | "
                 f"{r['score']:.1f} | {r['margin']:.2f} | {r['top'] or '—'} |")
    cov_bad = [r["cov"] for r in bad]
    if cov_bad:
        L += ["", f"> 错误作答的覆盖率中位 **{sorted(cov_bad)[len(cov_bad)//2]:.2f}**，"
                  f"最低 {min(cov_bad):.2f}、最高 {max(cov_bad):.2f}。"
                  "覆盖率高的原因不是证据足，而是问句里的通用词（补贴/合同/申请）在**别的文件**里也有。"]
    return "\n".join(L)


def main() -> None:
    rows = run()
    md = report(rows)
    out = Path("data/reports/missing.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md + "\n", encoding="utf-8")
    print(md)
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
