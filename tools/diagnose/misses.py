"""把 dev 集的"没命中"逐条归因：到底是前面几步没做好，还是检索排序的问题。

判据不是感觉，是三个可测的量：
  1. 金标文件在**不过滤**的排序里排第几（rank）——排得近就是排序问题
  2. 金标文件里最相关的那条条款，覆盖了提问词的多少（cov）——低于 0.5 说明
     用户用的词和文件里写的词对不上，这不是切分能修的
  3. 那条条款有多长（chars）——太长会被 BM25 的长度归一化压下去

用法：python -m tools.diagnose.misses [--split dev] [--out data/reports/misses.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from app.retrieval.index import tokenize
from app.retrieval.search import QUESTION_WORDS, search_result

QS = Path("eval/questions_v2.json")
OUT = Path("data/reports/misses.md")


def relevant(q: dict) -> bool:
    return q.get("category") == "official" and not q.get("ambiguous")


def cov_of(qt: set[str], text: str) -> float:
    return len(qt & set(tokenize(text))) / max(1, len(qt))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--k", type=int, default=900)
    args = ap.parse_args()

    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q["split"] == args.split and relevant(q)]
    print(f"dev official n={len(qs)}")

    rows = []
    for q in qs:
        qt = {t for t in tokenize(q["q"]) if t not in QUESTION_WORDS}
        # 不过滤：min_score 负、覆盖率门槛 0，看真实排序
        a = search_result(q["q"], k=args.k, min_score=-1.0,
                          min_coverage=-1.0, min_cand_cov=0.0)
        gold = q["gold_doc"]
        rank = None
        best = None
        for i, h in enumerate(a.hits, 1):
            if h.clause.doc_no == gold:
                if rank is None:
                    rank = i
                if best is None:
                    best = h
        # 金标文件里覆盖率最高的条款（用来判断"词对不上"还是"排不上"）
        max_cov, max_len = 0.0, 0
        if a.hits:
            same = [h for h in a.hits if h.clause.doc_no == gold]
            for h in same:
                c = cov_of(qt, h.clause.text)
                if c > max_cov:
                    max_cov, max_len = c, len(h.clause.text)
        rows.append(dict(id=q["id"], q=q["q"], gold=gold, rank=rank,
                         refused=a.refused, outcome=a.outcome,
                         top1=(a.hits[0].clause.doc_no if a.hits else ""),
                         top1_cov=(a.hits[0].coverage if a.hits else 0.0),
                         gold_cov=max_cov, gold_len=max_len,
                         reason=a.reason))

    miss = [r for r in rows if r["refused"] or r["rank"] != 1]
    hit = [r for r in rows if not r["refused"] and r["rank"] == 1]
    print(f"hit@1 {len(hit)}/{len(rows)} = {len(hit)/len(rows):.0%}；未命中 {len(miss)}")

    # 归因
    buckets: Counter = Counter()
    detail: dict[str, list] = {}
    for r in miss:
        if r["refused"]:
            k = f"拒答·{r['outcome']}"
        elif r["rank"] in (2, 3):
            k = "排序·金标在第2-3名"
        elif r["rank"] and r["rank"] <= 10:
            k = "排序·金标在第4-10名"
        elif r["rank"]:
            k = "排序·金标在10名之外"
        else:
            k = "召回·金标不在候选"
        buckets[k] += 1
        detail.setdefault(k, []).append(r)

    print("\n归因：")
    for k, v in buckets.most_common():
        print(f"  {k:24s} {v}")

    # 关键判别：没命中的那些，金标文件里到底有没有能覆盖提问词的话
    print("\n不命中的 39 条里，金标文件自身的覆盖情况：")
    for k, rs in detail.items():
        covs = [r["gold_cov"] for r in rs if r["gold_cov"] > 0]
        if not covs:
            print(f"  {k:24s} 无候选（金标文件根本没进候选）")
            continue
        print(f"  {k:24s} n={len(covs)} 覆盖中位 {statistics.median(covs):.2f} "
              f"最高 {max(covs):.2f}；条款长度中位 {int(statistics.median([r['gold_len'] for r in rs]))}")

    lines = [f"# dev 未命中归因（n={len(rows)}，hit@1 {len(hit)}）", ""]
    lines.append("| 类别 | 条数 | 金标条款覆盖中位 | 金标条款长度中位 |")
    lines.append("|---|---|---|---|")
    for k, v in buckets.most_common():
        rs = detail[k]
        covs = [r["gold_cov"] for r in rs]
        lens = [r["gold_len"] for r in rs if r["gold_len"]]
        lines.append(f"| {k} | {v} | {statistics.median(covs):.2f} | "
                     f"{int(statistics.median(lens)) if lens else '-'} |")
    lines += ["", "## 逐条", "", "| id | 排名 | 覆盖 | 条款长 | 问题 |", "|---|---|---|---|---|"]
    for r in miss:
        lines.append(f"| {r['id']} | {r['rank'] or '未进候选'} | {r['gold_cov']:.2f} | "
                     f"{r['gold_len']} | {r['q'][:40]} |")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
