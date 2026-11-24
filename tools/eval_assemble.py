"""装配策略对照：锚点（3 份文件 × 每份 20 条） vs 纯条款（top-30 × 每份 ≤8 条）。

同一批 70 道可核对题、同一检索器（含重排）、同一 L3 判据，只换装配。
如果纯条款不比锚点差，说明"文件级锚点"是我们自己加的限制。
"""
from __future__ import annotations

import json
import time

from app.answer import context as C
from app.retrieval import search as S
from tools.eval_answer import covered_sentences

QS = "eval/questions_v3.json"
ANSWERS = "eval/official_answers.json"


def main() -> None:
    from app.retrieval.search import _load

    _r, clauses, _c = _load()
    ALL = "".join(c.text for c in clauses)
    answers = json.load(open(ANSWERS, encoding="utf-8"))
    qs = [q for q in json.load(open(QS, encoding="utf-8"))
          if q.get("category") == "official" and q.get("split") == "dev"
          and not q.get("ambiguous") and not q.get("stale")]

    rows = []
    t0 = time.time()
    for i, q in enumerate(qs, 1):
        text = q.get("q_ctx") or q["q"]
        off = answers.get(q["id"], "")
        if not off or not covered_sentences(off, ALL)[0]:
            continue
        a = S.search_result(text, k=5)
        ev_a = C.assemble(text, a.hits) if a.hits else []
        ev_b = C.assemble_flat(S.ranked_clauses(text, k=30))
        row = {"id": q["id"], "gold": q["gold_doc"]}
        for key, ev in (("A", ev_a), ("B", ev_b)):
            blob = "".join(e.text for e in ev)
            hit, sent = covered_sentences(off, blob) if blob else (0, 0)
            row[key] = {"n": len(ev), "chars": len(blob), "hit": hit, "sent": sent,
                        "gold_in": any(e.doc_no == q["gold_doc"] for e in ev)}
        rows.append(row)
        print(f"{i}/{len(qs)} {q['id']} A={row['A']['hit']}/{row['A']['sent']} "
              f"B={row['B']['hit']}/{row['B']['sent']}", flush=True)

    def agg(key):
        ok = [r for r in rows if r[key]["sent"]]
        n = len(rows)
        return {
            "n": n,
            "items": sum(r[key]["n"] for r in rows) / n,
            "chars": sum(r[key]["chars"] for r in rows) / n,
            "at1": sum(1 for r in rows if r[key]["hit"] > 0) / n,
            "half": sum(1 for r in ok if r[key]["hit"] * 2 >= r[key]["sent"]) / n,
            "full": sum(1 for r in ok if r[key]["hit"] == r[key]["sent"]) / n,
            "sent": sum(r[key]["hit"] for r in rows) / max(1, sum(r[key]["sent"] for r in rows)),
            "gold": sum(1 for r in rows if r[key]["gold_in"]) / n,
        }

    a, b = agg("A"), agg("B")
    print(f"\n耗时 {time.time() - t0:.0f}s，题数 {len(rows)}")
    print(f"{'策略':<22}{'条/字':>12}{'金标在证据':>10}{'至少一句':>9}{'过半':>7}{'全句':>7}{'句子级':>8}")
    for label, x in (("A 锚点 3×20", a), ("B 纯条款 30×8", b)):
        print(f"{label:<22}{x['items']:>5.0f}/{x['chars']:>6.0f}{x['gold']:>10.1%}"
              f"{x['at1']:>9.1%}{x['half']:>7.1%}{x['full']:>7.1%}{x['sent']:>8.1%}")
    only_a = sum(1 for r in rows if r["A"]["hit"] > 0 and r["B"]["hit"] == 0)
    only_b = sum(1 for r in rows if r["B"]["hit"] > 0 and r["A"]["hit"] == 0)
    print(f"\n至少一句：只有 A 命中 {only_a} 题，只有 B 命中 {only_b} 题，都命中 "
          f"{sum(1 for r in rows if r['A']['hit'] > 0 and r['B']['hit'] > 0)} 题")
    json.dump({"A": a, "B": b, "only_a": only_a, "only_b": only_b, "rows": rows},
              open("data/reports/assemble-ablation.json", "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
