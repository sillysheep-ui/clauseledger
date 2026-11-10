"""评测指标的纯函数实现。只依赖标准库，可以脱离系统单测。

三条约定（来自 AUDIT.md R4）：

1. 凡是比例，一律给 Wilson 区间，不给裸点估计。n=20 的样本撑不起"提升 10pp"这种结论。
2. 稳定性用 pass^k 的无偏估计 C(c,k)/C(n,k)，不用 pass@k——后者只是乐观上界。
3. "把不确定说成确定"单列为致命错误，不与普通错误混算。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

# 四态判定 + 明确拒答。顺序固定，混淆矩阵按此排列。
VERDICTS: tuple[str, ...] = (
    "satisfied",
    "unsatisfied",
    "unknown",
    "not_applicable",
    "no_policy",
)

# 用例级结局。STALE 与 ERROR 必须和 FAIL 分开：
#   STALE = 金标指向的条款已经解析不到了（语料/解析器变了），不是系统错
#   ERROR = 系统没跑完或产物不合结构，不是判断错
CASE_OUTCOMES: tuple[str, ...] = ("PASS", "FAIL", "STALE", "ERROR")


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """比例的 Wilson 95% 区间（默认 z=1.96）。

    >>> round(wilson_interval(15, 20)[0], 3), round(wilson_interval(15, 20)[1], 3)
    (0.531, 0.888)
    """
    if n <= 0:
        raise ValueError("n 必须为正")
    if not 0 <= successes <= n:
        raise ValueError("successes 必须在 [0, n] 内")

    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


@dataclass(frozen=True)
class Rate:
    """带区间的比例。报告里只该出现这个，不该出现单独的 0.75。"""

    successes: int
    n: int
    p: float
    lo: float
    hi: float

    def __str__(self) -> str:
        return f"{self.successes}/{self.n} = {self.p:.1%} [{self.lo:.1%}, {self.hi:.1%}]"


def rate(successes: int, n: int, z: float = 1.96) -> Rate:
    lo, hi = wilson_interval(successes, n, z)
    return Rate(successes, n, successes / n, lo, hi)


def confusion_matrix(
    gold: Sequence[str],
    pred: Sequence[str],
    labels: Sequence[str] = VERDICTS,
) -> dict[str, dict[str, int]]:
    """金标 × 预测 的混淆矩阵，形如 cm[gold][pred] = 次数。"""
    if len(gold) != len(pred):
        raise ValueError("gold 与 pred 长度必须一致")

    valid = set(labels)
    cm = {g: {p: 0 for p in labels} for g in labels}
    for g, p in zip(gold, pred):
        if g not in valid:
            raise ValueError(f"未知金标: {g}")
        if p not in valid:
            raise ValueError(f"未知预测: {p}")
        cm[g][p] += 1
    return cm


def per_class_prf(
    cm: dict[str, dict[str, int]],
    labels: Sequence[str] = VERDICTS,
) -> dict[str, dict[str, float]]:
    """每类的 precision / recall / f1 / support。"""
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = cm[label][label]
        support = sum(cm[label].values())
        predicted = sum(cm[g][label] for g in labels)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(support),
        }
    return out


def macro_f1(
    cm: dict[str, dict[str, int]],
    labels: Sequence[str] = VERDICTS,
) -> float:
    """只对有金标样本的类取平均——金标里没有的类不应拉低分数。"""
    prf = per_class_prf(cm, labels)
    scored = [v["f1"] for v in prf.values() if v["support"] > 0]
    return sum(scored) / len(scored) if scored else 0.0


def false_satisfied_indices(gold: Sequence[str], pred: Sequence[str]) -> list[int]:
    """致命错误：金标不是 satisfied，系统却说 satisfied。

    这是唯一一条硬红线。它和"漏判"不是一个量级：漏判让人多跑一趟，
    误判让人白等一年。
    """
    return [
        i
        for i, (g, p) in enumerate(zip(gold, pred))
        if g != "satisfied" and p == "satisfied"
    ]


def pass_k(n_trials: int, successes: int, k: int) -> float:
    """pass^k 的无偏估计：从 n 次试验里无放回抽 k 次，全对的概率。

    n=3, c=2, k=2 -> 1/3。这就是"三次里两次对"在稳定性口径下的真实值，
    而不是 2/3。
    """
    if k <= 0:
        raise ValueError("k 必须为正")
    if not 0 <= successes <= n_trials:
        raise ValueError("successes 必须在 [0, n_trials] 内")
    if k > n_trials:
        raise ValueError("k 不能大于试验次数")
    if successes < k:
        return 0.0
    return math.comb(successes, k) / math.comb(n_trials, k)


def mcnemar_exact(b: int, c: int) -> float:
    """配对比较的精确 McNemar 检验，返回双侧 p 值。

    b = A 对 B 错 的用例数，c = A 错 B 对。n 小的时候必须用精确法，
    卡方近似在 b+c<25 时不可靠。
    """
    if b < 0 or c < 0:
        raise ValueError("b、c 不能为负")
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def case_outcome_summary(outcomes: Sequence[str]) -> dict[str, int]:
    """用例结局计数，并校验没有未知值。报告里这四个数必须加起来等于用例总数。"""
    unknown = set(outcomes) - set(CASE_OUTCOMES)
    if unknown:
        raise ValueError(f"未知的用例结局: {sorted(unknown)}")
    counted = Counter(outcomes)
    return {k: counted.get(k, 0) for k in CASE_OUTCOMES}
