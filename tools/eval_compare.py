"""配对比较两次评测：说清"几条变好、几条变坏"，而不是只比两个百分比。

两个百分比之间的差，可能来自题目难度不同；同一批题的前后对比才能归因到改动。

用法：
    python -m tools.eval_compare eval/reports/A-dev.json eval/reports/B-dev.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

random.seed(0)


def mcnemar_exact(b: int, c: int) -> float:
    """b = 变坏数，c = 变好数；返回精确二项检验的双侧 p。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def bootstrap_ci(deltas: list[int], iters: int = 5000) -> tuple[float, float]:
    n = len(deltas)
    if n == 0:
        return (0.0, 0.0)
    means = []
    for _ in range(iters):
        s = sum(deltas[random.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    a = ap.parse_args()
    A = json.loads(Path(a.before).read_text("utf-8"))
    B = json.loads(Path(a.after).read_text("utf-8"))
    ma = {r["id"]: r for r in A["rows"]}
    mb = {r["id"]: r for r in B["rows"]}
    ids = sorted(set(ma) & set(mb))
    imp = wor = 0
    deltas = []
    per_cat: dict[str, list[int]] = {}
    for i in ids:
        x, y = ma[i]["ok1"], mb[i]["ok1"]
        d = y - x
        deltas.append(d)
        imp += d == 1
        wor += d == -1
        per_cat.setdefault(ma[i]["cat"], []).append(d)
    lo, hi = bootstrap_ci(deltas)
    print(f"配对 {len(ids)} 条：变好 {imp}，变坏 {wor}，不变 {len(ids)-imp-wor}")
    print(f"总体 ok1 变化 {sum(deltas)/len(ids):+.1%}  95%CI {lo:+.1%} ~ {hi:+.1%}")
    print(f"McNemar 精确检验 p = {mcnemar_exact(wor, imp):.4f}"
          f"{'  ← 差异显著' if mcnemar_exact(wor, imp) < 0.05 else '  （样本量不足以判定）'}")
    for cat, ds in per_cat.items():
        print(f"  {cat:14} n={len(ds):2} 净变化 {sum(ds):+d}")
    if ma[ids[0]]["split"] == "test":
        print("\n注意：这是 test 集。跑过就算用掉一次，别拿它调参。")


if __name__ == "__main__":
    main()
