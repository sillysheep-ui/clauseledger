"""给大模型看到的上下文，覆盖了多少官方答案。

和 `tools/eval_recall.py` 的区别：
  - eval_recall 量的是「候选池」里有没有答案（召回层的上界）；
  - 这里量的是「真正进 prompt 的那段文字」里有没有答案（排序 + 装配之后的结果）。

三件事：
  1. ctx_recall：官方答案的句子，有多少能在**装配后的上下文**里找到（4-gram ≥0.6，同漏斗 S5 口径）
  2. ctx_doc：上下文里的证据，有多大比例来自金标文件（噪声比例）
  3. answerable：至少一句 / 过半 / 全句 三种覆盖

用法：
  python -m tools.eval_context --k 5 --write
  python -m tools.eval_context --k 5 --no-parent   # 只给命中的条款，不展开父条/邻居
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.answer import context as ctx
from app.retrieval import search as S
from tools.eval_answer import covered_sentences

QS = "eval/questions_v3.json"
ANSWERS = "eval/official_answers.json"
OUT = Path("data/reports/context-coverage.json")


def load_questions() -> list[dict]:
    qs = json.load(open(QS, encoding="utf-8"))
    return [q for q in qs
            if q.get("category") == "official"
            and q.get("split") == "dev"
            and not q.get("ambiguous") and not q.get("stale")]


def run(k: int, with_parent: bool, limit: int | None) -> dict:
    qs = load_questions()
    if limit:
        qs = qs[:limit]
    answers = json.load(open(ANSWERS, encoding="utf-8"))
    _ret, clauses, _children = S._load()
    ALL = "".join(c.text for c in clauses)
    rows = []
    t0 = time.time()
    for q in qs:
        text = q.get("q_ctx") or q["q"]
        official = answers.get(q["id"], "")
        traceable = covered_sentences(official, ALL)[0] > 0 if official else False
        a = S.search_result(text, k=k)
        ev = ctx.evidence_for(text) if a.hits else []
        if not with_parent:
            ev = [e for e in ev if e.role == "hit"]
        blob = "".join(e.text for e in ev)
        n_hit, n_sent = covered_sentences(official, blob) if official else (0, 0)
        gold_ev = sum(1 for e in ev if e.doc_no == q["gold_doc"])
        rows.append({
            "id": q["id"],
            "gold_doc": q["gold_doc"],
            "kind": a.kind,
            "traceable": traceable,
            "hit_sent": n_hit,
            "n_sent": n_sent,
            "any": n_hit > 0,
            "half": n_hit * 2 >= n_sent > 0,
            "all": n_sent > 0 and n_hit == n_sent,
            "n_ev": len(ev),
            "gold_ev": gold_ev,
            "chars": len(blob),
        })
    dt = time.time() - t0
    out = {"k": k, "with_parent": with_parent, "n": len(rows),
           "seconds": round(dt, 1), "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def report(o: dict) -> None:
    rows = [r for r in o["rows"] if r["traceable"]]
    n = len(rows)
    if not n:
        print("没有可核对官方答案的题")
        return
    any_ = sum(r["any"] for r in rows)
    half = sum(r["half"] for r in rows)
    all_ = sum(r["all"] for r in rows)
    sent = sum(r["hit_sent"] for r in rows) / max(1, sum(r["n_sent"] for r in rows))
    ev = sum(r["n_ev"] for r in rows)
    gold = sum(r["gold_ev"] for r in rows)
    chars = sum(r["chars"] for r in rows) / max(1, n)
    print(f"\n== k={o['k']} parent={o['with_parent']}  n={n}（可核对官方答案） ==")
    print(f"  至少一句 {any_}/{n} = {any_ / n:.1%}   过半 {half}/{n} = {half / n:.1%}"
          f"   全句 {all_}/{n} = {all_ / n:.1%}   句子级 {sent:.1%}")
    print(f"  平均证据 {ev / n:.1f} 条 / {chars:.0f} 字；其中来自金标文件 {gold / max(1, ev):.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-parent", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    o = run(a.k, not a.no_parent, a.limit)
    report(o)


if __name__ == "__main__":
    main()
