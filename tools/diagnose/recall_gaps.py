"""召回缺口诊断：金标没进前 10 的题，到底缺在哪一环。

分三类，处理方式完全不同：
  A 词不在文件里   —— 问题用词与文件用词不通（需要改写/别名词典）
  B 词在、排不上   —— 文件里有这些词，但被长度/竞争压到 10 名外（排序问题）
  C 单路没找到     —— BM25 找不到、稠密能找到（融合问题）

用法：python -m tools.diagnose.recall_gaps [--ctx] [--k 10]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.retrieval import dense
from app.retrieval.index import tokenize
from app.retrieval.search import _load, search_result

QS = Path("eval/questions_v3.json")
RELAX = dict(min_score=0.0, min_cand_cov=0.0, min_coverage=0.0,
             min_answer=-1.0, min_score_strong=-1.0, min_margin_strong=-1.0)


def doc_texts() -> dict[str, str]:
    s = Path("data")
    out: dict[str, list[str]] = {}
    for p in sorted((s / "processed").glob("*.json")):
        rec = json.loads(p.read_text("utf-8"))
        no = rec.get("doc_no")
        if no:
            out.setdefault(no, []).extend(c.get("text", "") for c in rec.get("clauses", []))
    return {k: "".join(v) for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", action="store_true")
    ap.add_argument("--k", type=int, default=10)
    a = ap.parse_args()

    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q.get("category") == "official" and q.get("split") == "dev"]
    texts = doc_texts()
    _, clauses, children = _load()
    rows = []
    for q in qs:
        text = q.get("q_ctx") if (a.ctx and q.get("q_ctx")) else q["q"]
        ans = search_result(text, k=a.k, **RELAX)
        seen = [h.clause.doc_no for h in ans.hits]
        if q["gold_doc"] in seen:
            continue
        gold = q["gold_doc"]
        terms = [t for t in tokenize(text) if len(t) >= 2]
        dt = set(tokenize(texts.get(gold, "")))
        cov = (sum(1 for t in terms if t in dt) / len(terms)) if terms else 0.0
        drank = None
        if dense.available():
            for i, (ci, _s) in enumerate(dense.rank(text, children, k=100), 1):
                if clauses[children[ci]["pid"]].doc_no == gold:
                    drank = i
                    break
        rows.append((q["id"], cov, drank, bool(texts.get(gold)), len(texts.get(gold, ""))))
    print(f"未进前 {a.k}：{len(rows)} 条")
    a_cnt = sum(1 for r in rows if r[1] < 0.6)
    b_cnt = sum(1 for r in rows if r[1] >= 0.6)
    c_cnt = sum(1 for r in rows if r[2] is not None)
    print(f"A 词覆盖<60%（词不通）：{a_cnt}　B 词覆盖≥60%（排不上）：{b_cnt}　C 稠密前100能找到：{c_cnt}")
    print(f"{'id':10} {'cov':>5} {'dense':>6} {'字数':>6}")
    for r in sorted(rows, key=lambda x: x[1]):
        print(f"{r[0]:10} {r[1]:5.0%} {str(r[2] or '-'):>6} {r[4]:6}")


if __name__ == "__main__":
    main()
