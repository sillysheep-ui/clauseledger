"""口语题评测：同一批金标，只改问法。

这一层测的是"用户不会用政策语言提问"时的鲁棒性，与官方题（`eval_v2`）分开报：
官方题的问法本来就抄自政策，hit@1 高是理所当然；口语题才逼出词汇鸿沟。

用法：
  python -m tools.eval_paraphrase                 # 默认 hybrid
  CLAUSELEDGER_RECALL=bm25 python -m tools.eval_paraphrase
  python -m tools.eval_paraphrase --k 5           # 看 R@5
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from app.retrieval import search as S
from tools.eval_v2 import is_hit, wilson

QS = Path(os.environ.get("CLAUSEDGER_PARAPHRASE", "eval/questions_paraphrase.json"))


def run(k: int = 3) -> dict:
    qs = json.load(open(QS))
    rows, kinds = [], Counter()
    hit1 = hitk = 0
    for q in qs:
        a = S.search_result(q["q"], k=k)
        kinds[a.kind] += 1
        top = a.hits[0] if a.hits else None
        h1 = bool(top) and is_hit(top, q["gold_doc"], q["gold_title"])
        hk = any(is_hit(h, q["gold_doc"], q["gold_title"]) for h in a.hits)
        hit1 += h1
        hitk += hk
        rows.append({"id": q["id"], "q": q["q"], "kind": a.kind,
                     "hit1": h1, "hitk": hk,
                     "top": top.clause.doc_no if top else None,
                     "gold": q["gold_doc"]})
    n = len(qs)
    lo1, hi1 = wilson(hit1, n)
    return {"n": n, "recall": S.recall_mode(), "k": k,
            "hit1": hit1 / n, "hit1_ci": (lo1, hi1),
            f"hit{k}": hitk / n,
            "kinds": dict(kinds), "rows": rows}


def report(r: dict) -> str:
    lo, hi = r["hit1_ci"]
    k = r["k"]
    lines = [f"口语题 n={r['n']}  召回={r['recall']}",
             f"hit@1 = {r['hit1']:.1%} ({lo:.0%}–{hi:.0%})   "
             f"hit@{k} = {r[f'hit{k}']:.1%}",
             "结局：" + " / ".join(f"{a}={b}" for a, b in sorted(r["kinds"].items()))]
    bad = [x for x in r["rows"] if not x["hit1"]]
    if bad:
        lines.append(f"未命中 {len(bad)} 条：")
        for x in bad[:12]:
            lines.append(f"  {x['id']} [{x['kind']}] {x['q'][:34]} → 得 {x['top']} / 期望 {x['gold']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    r = run(a.k)
    text = report(r)
    print(text)
    if a.write:
        d = Path("eval/reports")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"paraphrase-{r['recall']}.json").write_text(
            json.dumps({k: v for k, v in r.items() if k != "rows"},
                       ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
