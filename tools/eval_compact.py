"""证据预算消融：同一批题、同一判据，只改注入上下文的大小。

回答的问题：「少而整」到底该给多少——2,000 / 4,000 / 6,000 字，还是不限？
判据沿用 L3（卡片证据含官方答案句，4-gram ≥0.6），分母只含可核对的 70 题。
"""
from __future__ import annotations

import argparse
import json
import time

from app.tools import service as T
from tools.eval_answer import covered_sentences

QS = "eval/questions_v3.json"
ANSWERS = "eval/official_answers.json"


def load_questions() -> list[dict]:
    qs = json.load(open(QS, encoding="utf-8"))
    return [q for q in qs
            if q.get("category") == "official" and q.get("split") == "dev"
            and not q.get("ambiguous") and not q.get("stale")]


def agg(rows: list[dict], key: int | str, n: int) -> dict:
    ok = [r for r in rows if r.get(key)]
    if not ok:
        return {}
    at1 = sum(1 for r in ok if r[key]["hit"] > 0)
    # 拒答时 sent=0：不能算"过半/全句覆盖"，否则 0>=0 会把拒答记成覆盖
    half = sum(1 for r in ok if r[key]["sent"] and r[key]["hit"] * 2 >= r[key]["sent"])
    full = sum(1 for r in ok if r[key]["sent"] and r[key]["hit"] == r[key]["sent"])
    hit = sum(r[key]["hit"] for r in ok)
    sent = sum(r[key]["sent"] for r in ok)
    chars = sum(r[key]["chars"] for r in ok) / len(ok)
    items = sum(r[key]["n"] for r in ok) / len(ok)
    return {"n": len(ok), "at1": at1 / len(ok), "half": half / len(ok),
            "full": full / len(ok), "sent": hit / sent if sent else 0,
            "chars": chars, "items": items}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budgets", default="2000,4000,6000")
    ap.add_argument("--items", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    budgets = [int(b) for b in args.budgets.split(",") if b]

    from app.retrieval.search import _load

    _r, clauses, _c = _load()
    ALL = "".join(c.text for c in clauses)
    answers = json.load(open(ANSWERS, encoding="utf-8"))
    qs = load_questions()
    if args.limit:
        qs = qs[: args.limit]

    rows = []
    t0 = time.time()
    for i, q in enumerate(qs, 1):
        text = q.get("q_ctx") or q["q"]
        off = answers.get(q["id"], "")
        if not off:
            continue
        trace, _ = covered_sentences(off, ALL)
        if not trace:
            continue
        full = T.evidence(text)
        row = {"id": q["id"], "refused": full["refused"],
               "full": {"n": len(full["evidence"]),
                        "chars": sum(len(e["text"]) for e in full["evidence"]),
                        "hit": 0, "sent": 0}}
        if not full["refused"]:
            blob = "".join(e["text"] for e in full["evidence"])
            row["full"]["hit"], row["full"]["sent"] = covered_sentences(off, blob)
        for b in budgets:
            its, used = T.compact(full["evidence"], max_items=args.items, budget=b)
            blob = "".join(e["text"] for e in its)
            hit, sent = covered_sentences(off, blob) if blob else (0, 0)
            row[b] = {"n": len(its), "chars": used, "hit": hit, "sent": sent}
        rows.append(row)
        print(f"{i}/{len(qs)} {q['id']} refused={row['refused']}", flush=True)

    print(f"\n耗时 {time.time() - t0:.0f}s，可核对题 {len(rows)}")
    cols = [("full", "全部装配")] + [(b, f"{b} 字") for b in budgets]
    print(f"{'配置':<12}{'条/字':>12}{'至少一句':>10}{'过半':>8}{'全句':>8}{'句子级':>8}")
    out = {}
    for key, label in cols:
        a = agg(rows, key, len(rows))
        if not a:
            continue
        out[str(key)] = a
        print(f"{label:<12}{a['items']:>5.0f}/{a['chars']:>6.0f}"
              f"{a['at1']:>9.1%}{a['half']:>8.1%}{a['full']:>8.1%}{a['sent']:>8.1%}")
    json.dump(out, open("data/reports/compact-ablation.json", "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
