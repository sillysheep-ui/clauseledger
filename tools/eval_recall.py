"""召回层对照：只换检索器，排序与决策保持不变。

口径（docs/EVAL.md §4.7 的 L1/L4）：
  R@k    金标文件是否出现在前 k 个「不同文件」里（doc 级，只数文号）
  ans@k  前 k 条候选条款里，有没有一条逐字含有官方答案中的某一句（clause 级）

分母：ans@k 只算「官方答案能在语料里逐字找到」的题，否则它考的是转述能力。
用法：
  CLAUSELEDGER_RECALL=bm25 python -m tools.eval_recall --write
  CLAUSELEDGER_RECALL=dense python -m tools.eval_recall --write
  CLAUSELEDGER_RECALL=hybrid python -m tools.eval_recall --write
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from app.retrieval import search as S
from tools.eval_answer import covered_sentences

QS = "eval/questions_v3.json"
ANSWERS = "eval/official_answers.json"
OUTDIR = Path("data/reports")
KS = (1, 5, 10, 20)




def load_questions() -> list[dict]:
    qs = json.load(open(QS, encoding="utf-8"))
    return [q for q in qs
            if q.get("category") == "official"
            and q.get("split") == "dev"
            and not q.get("ambiguous") and not q.get("stale")]


def run(mode: str, pool_size: int = 50) -> dict:
    qs = load_questions()
    answers = json.load(open(ANSWERS, encoding="utf-8"))
    # 分母用「整库」判断，不能用候选池——否则分母本身就被检索器选过了。
    _ret, _clauses, _children = S._load()
    ALL = "".join(c.text for c in _clauses)
    rows = []
    t0 = time.time()
    for q in qs:
        text = q.get("q_ctx") or q["q"]
        p = S.pool(text, pool_size=pool_size)
        if not rows and p.mode != mode:
            # 静默降级是这里最危险的失败：以为在测 dense，其实跑的是 bm25。
            raise SystemExit(
                f"召回模式没生效：请求 {mode}，实际 {p.mode}。"
                "dense 需要模型可用；离线环境请设 HF_HUB_OFFLINE=1。")
        cands = sorted(p.cands, key=lambda c: -c.score)
        docs: list[str] = []
        for c in cands:
            if c.clause.doc_no not in docs:
                docs.append(c.clause.doc_no)
        official = answers.get(q["id"], "")
        # 官方答案能不能在**整库**里找到相近表述（4-gram 召回 ≥0.6，与漏斗 S5 同口径）
        found = covered_sentences(official, ALL)[0] > 0 if official else None
        ans_hits = {}
        for k in KS:
            blob = "".join(c.clause.text for c in cands[:k])
            ans_hits[k] = covered_sentences(official, blob)[0] > 0 if official else None
        rows.append({
            "id": q["id"],
            "gold_doc": q["gold_doc"],
            "docs": docs,
            "r": {k: (q["gold_doc"] in docs[:k]) for k in KS},
            "ans": ans_hits,
            "usable": bool(found),
        })
    dt = time.time() - t0
    n = len(rows)
    out = {"mode": mode, "n": n, "seconds": round(dt, 1),
           "ms_per_query": round(dt * 1000 / max(1, n), 1), "rows": rows}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"recall-layer-{mode}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def report(o: dict) -> None:
    rows = o["rows"]
    n = len(rows)
    usable = [r for r in rows if r["usable"]]
    print(f"\n== {o['mode']}  n={n}  {o['ms_per_query']} ms/题 ==")
    cells = []
    for k in KS:
        r = sum(1 for x in rows if x["r"][k])
        cells.append(f"R@{k} {r}/{n}={r / n:.1%}")
    print("  " + "  ".join(cells))
    if usable:
        m = len(usable)
        cells = []
        for k in KS:
            a = sum(1 for x in usable if x["ans"][k])
            cells.append(f"ans@{k} {a}/{m}={a / m:.1%}")
        print("  " + "  ".join(cells) + f"   (ans 分母 {m}，官方答案可逐字核对的题)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-size", type=int, default=50)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    mode = os.environ.get("CLAUSELEDGER_RECALL", "bm25").lower()
    o = run(mode, a.pool_size)
    report(o)


if __name__ == "__main__":
    main()
