"""命令行提问：只看检索与引用，不含判定。

用法：
    python -m tools.ask "丧假有几天"
    python -m tools.ask "工伤认定需要哪些材料" -k 5
"""
from __future__ import annotations

import argparse

from app.retrieval.search import evidence_gap, search


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--only-effective", action="store_true", help="只用现行有效条款")
    a = ap.parse_args()

    hits = search(a.question, k=a.k, status=("effective",) if a.only_effective else None)
    print(f"问：{a.question}\n")
    if not hits:
        print("答：证据不足。" + (evidence_gap(a.question, hits) or ""))
        return

    for i, h in enumerate(hits, 1):
        c = h.clause
        flag = {"effective": "✓现行", "expired": "✗已失效",
                "pending": "·未生效", "unknown": "?效力未知"}[c.status]
        print(f"[{i}] {h.score:5.1f} cov={h.coverage:.0%} {flag}  {c.citation}")
        if c.effective_to:
            print(f"     有效期至 {c.effective_to}")
        body = c.text.replace("\n", " ").strip()
        print(f"     {body[:160]}{'…' if len(body) > 160 else ''}\n")

    gap = evidence_gap(a.question, hits)
    if gap:
        print("提示：" + gap)


if __name__ == "__main__":
    main()
