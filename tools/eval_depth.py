"""漏斗深度扫描：候选池取多深，金标才进得了证据。

只回答一个问题：把我们实际喂给模型的那 25~30 条条款，是从多深的候选池里挑出来的？
现在的池子是 60 条，重排只看前 40 条。如果金标在第 61~150 条里，我们永远拿不到它。

做法：固定重排窗口（40），只改候选池深度（60 / 150 / 300），对 dev 105 题量两个数：
  - gold_in_pool：金标文件在候选池里（召回上界）
  - gold_in_ev：金标文件进了装配后的证据（真正喂给模型的）
用法：python -m tools.eval_depth
"""
from __future__ import annotations

import json
import time

from app.answer import context as ctx
from app.retrieval import search as S

QS = "eval/questions_v3.json"
DEPTHS = (60, 150, 300)

def load() -> list[dict]:
    qs = json.load(open(QS, encoding="utf-8"))
    return [q for q in qs
            if q.get("category") == "official" and q.get("split") == "dev"
            and not q.get("ambiguous") and not q.get("stale")]

def run(depth: int, qs: list[dict]) -> dict:
    orig = S.pool
    def patched(query, k=8, **kw):
        kw["pool_size"] = depth
        return orig(query, k, **kw)
    S.pool = patched
    n = len(qs)
    in_pool = in_ev = 0
    ev_items = ev_chars = 0
    t0 = time.time()
    try:
        for q in qs:
            text = q.get("q_ctx") or q["q"]
            gold = q["gold_doc"]
            p = S.pool(text, k=30)
            if any(cd.clause.doc_no == gold for cd in p.cands):
                in_pool += 1
            hits = S.ranked_clauses(text, k=30)
            ev = ctx.assemble_flat(hits)
            if any(e.doc_no == gold for e in ev):
                in_ev += 1
            ev_items += len(ev)
            ev_chars += sum(len(e.text) for e in ev)
    finally:
        S.pool = orig
    return {"depth": depth, "n": n, "gold_in_pool": [in_pool, n],
            "gold_in_ev": [in_ev, n], "avg_items": round(ev_items / n, 1),
            "avg_chars": round(ev_chars / n), "seconds": round(time.time() - t0, 1)}

def main() -> None:
    qs = load()
    rows = []
    for d in DEPTHS:
        r = run(d, qs)
        rows.append(r)
        print(f"pool={d:>3}  gold_in_pool {r['gold_in_pool'][0]}/{r['n']}  "
              f"gold_in_ev {r['gold_in_ev'][0]}/{r['n']}  "
              f"证据 {r['avg_items']} 条/{r['avg_chars']} 字  {r['seconds']}s", flush=True)
    json.dump(rows, open("data/reports/depth.json", "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
