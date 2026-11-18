"""从语料里挖候选词（互信息 + 左右熵），给人筛，不自动进词典。

    python -m tools.mine_terms --top 30 --min-freq 10

为什么要它：手写词表是"失败驱动"的——只有被问到、答错了，才会想到补一个词。
挖候选是"语料驱动"的：它不关心有没有人问过。

**它只产出候选，不产出结论。** 前 N 个里通常有三成是切错的边界
（"级博物馆" "校就读的"），必须人看。
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import jieba

CLAUSES = Path("data/index/clauses.jsonl")


def cjk(s: str) -> bool:
    return all("\u4e00" <= ch <= "\u9fa5" for ch in s)


def entropy(c: Counter) -> float:
    tot = sum(c.values()) or 1
    return -sum((v / tot) * math.log(v / tot) for v in c.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-freq", type=int, default=10)
    ap.add_argument("--max-n", type=int, default=4)
    ap.add_argument("--min-cohesion", type=float, default=20.0)
    ap.add_argument("--min-entropy", type=float, default=0.5)
    a = ap.parse_args()

    corpus = "".join(json.loads(l)["text"].replace("\n", "")
                     for l in CLAUSES.read_text("utf-8").splitlines())
    n_chars = len(corpus)

    cnt: Counter = Counter()
    for n in range(2, a.max_n + 1):
        for i in range(len(corpus) - n + 1):
            s = corpus[i:i + n]
            if cjk(s):
                cnt[s] += 1
    cnt = Counter({k: v for k, v in cnt.items() if v >= a.min_freq})

    left: dict[str, Counter] = defaultdict(Counter)
    right: dict[str, Counter] = defaultdict(Counter)
    for w in cnt:
        n = len(w)
        start = 0
        while True:
            i = corpus.find(w, start)
            if i < 0:
                break
            if i > 0:
                left[w][corpus[i - 1]] += 1
            if i + n < len(corpus):
                right[w][corpus[i + n]] += 1
            start = i + 1

    def cohesion(w: str) -> float:
        parts = []
        for k in range(1, len(w)):
            x, y = w[:k], w[k:]
            if cnt.get(x, 0) < 3 or cnt.get(y, 0) < 3:
                continue
            parts.append(cnt[w] * n_chars / (cnt[x] * cnt[y]))
        return min(parts) if parts else 0.0

    jieba.initialize()
    rows = []
    for w, c in cnt.items():
        co = cohesion(w)
        if co < a.min_cohesion:
            continue
        e = min(entropy(left[w]), entropy(right[w]))
        if e < a.min_entropy:
            continue
        rows.append((w, c, co, e, w in jieba.dt.FREQ))
    rows.sort(key=lambda r: (-r[2], -r[1]))

    print(f"# 候选词（语料 {n_chars} 字，{len(rows)} 个候选）\n")
    print("| 词 | 频次 | 凝固度 | 左右熵 | jieba 词典 |")
    print("|---|---|---|---|---|")
    for w, c, co, e, known in rows[: a.top]:
        print(f"| {w} | {c} | {co:.0f} | {e:.2f} | {'已知' if known else '**新**'} |")
    new = sum(1 for r in rows if not r[4])
    print(f"\n候选里 jieba 不认识的有 {new} 个。加之前先问：它是词，还是切错的边界？")


if __name__ == "__main__":
    main()
