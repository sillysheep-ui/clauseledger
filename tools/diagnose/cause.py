"""把"检索失败"再往下解剖一刀：到底是**切分**把答案切碎了，还是**排序**把它压在下面。

    python -m tools.diagnose.cause

对每条 S5 失败的题，量三个覆盖率（都用实词，去水分词）：

    doc_cov    提问实词在**整份金标文件正文**里的覆盖
    clause_cov 提问实词在**该文件最好那一条条款**里的覆盖
    hit_rank   这条最好条款在检索结果里的名次（不过滤）

判读：
    doc_cov 低            → 词根本不在这份文件里（词汇不匹配，召回层问题）
    doc_cov 高 / clause 低 → 答案被切碎了（切分层问题）
    clause_cov 高 / 排名差 → 排序问题（BM25）
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from app.retrieval.search import content_terms, search, tokenize
from tools.diagnose.attribute import run as attribute_run

PROCESSED = Path("data/processed")


@dataclass
class Cause:
    id: str
    doc_cov: float
    clause_cov: float
    clause_chars: int
    top1_chars: int
    kind: str


def cov(terms: set[str], text: str) -> float:
    if not terms:
        return 0.0
    toks = set(tokenize(text))
    return len(terms & toks) / len(terms)


def classify(terms: set[str], rec: dict, hits) -> Cause:
    body = "\n".join(b.get("text", "") for b in rec.get("blocks") or [])
    dc = cov(terms, body)
    best, bc = None, 0.0
    for c in rec.get("clauses") or []:
        v = cov(terms, c.get("text", ""))
        if v > bc:
            best, bc = c, v
    rank = 0
    if best is not None:
        for i, h in enumerate(hits, 1):
            if (h.clause.doc_no == rec.get("doc_no")
                    and h.clause.path == best["path"]
                    and h.clause.text[:40] == best["text"][:40]):
                rank = i
                break
    top1 = hits[0].clause.text if hits else ""
    # 先看"切分/词汇"，再看"排序/闸门"——前面的问题会伪装成后面的问题
    if dc < 0.6:
        kind = "词不在文件里（词汇不匹配）"
    elif bc < 0.6:
        kind = "答案被切碎（切分）"
    elif rank == 1:
        kind = "条款排第一却被拒（闸门）"
    elif 2 <= rank <= 10:
        kind = "条款在、排不上（排序）"
    else:
        kind = "条款在候选外（召回）"
    return Cause("", dc, bc, len(best["text"]) if best else 0, len(top1), kind)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    args = ap.parse_args(argv)

    res = attribute_run(args.split)
    docs = {}
    for p in PROCESSED.glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        if d.get("doc_no"):
            docs[d["doc_no"]] = d

    rows, unknown = [], 0
    for r in res["by_stage"].get("S5", []):
        q = next((x for x in json.loads(Path("eval/questions_v2.json").read_text("utf-8"))
                  if x["id"] == r.id), None)
        rec = docs.get(r.gold_doc)
        if not q or not rec:
            unknown += 1
            continue
        terms = content_terms(set(tokenize(q["q"])))
        hits = search(q["q"], k=50)
        c = classify(terms, rec, hits)
        c.id = r.id
        rows.append(c)

    print(f"S5 失败 {len(rows)} 条（{unknown} 条缺文档无法判）\n")
    print(f"{'id':<10}{'doc_cov':>8}{'clause':>8}{'条款字数':>9}{'top1字数':>9}  判读")
    for c in sorted(rows, key=lambda x: (x.kind, -x.doc_cov)):
        print(f"{c.id:<10}{c.doc_cov:>8.2f}{c.clause_cov:>8.2f}{c.clause_chars:>9}"
              f"{c.top1_chars:>9}  {c.kind}")

    print()
    tally: dict[str, int] = {}
    for c in rows:
        tally[c.kind] = tally.get(c.kind, 0) + 1
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
