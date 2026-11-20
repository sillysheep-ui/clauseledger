"""指标模块的单测。数字都是手算过的，改实现必须同时改期望值。"""

from __future__ import annotations

import pytest

from app.eval.metrics import (
    VERDICTS,
    case_outcome_summary,
    confusion_matrix,
    false_satisfied_indices,
    macro_f1,
    mcnemar_exact,
    pass_k,
    per_class_prf,
    rate,
    wilson_interval,
)


class TestWilson:
    def test_15_of_20(self):
        """15/20 的区间是 53%~89%，不是 96%。区间宽度决定了 20 条样本能下多硬的结论。"""
        lo, hi = wilson_interval(15, 20)
        assert lo == pytest.approx(0.531, abs=0.002)
        assert hi == pytest.approx(0.888, abs=0.002)

    def test_all_failures(self):
        lo, hi = wilson_interval(0, 10)
        assert lo == 0.0
        assert hi == pytest.approx(0.278, abs=0.002)

    def test_all_successes_stays_below_one(self):
        """10/10 也不算 100%，上界只有约 72%。"""
        lo, hi = wilson_interval(10, 10)
        assert hi == 1.0
        assert lo == pytest.approx(0.722, abs=0.002)

    def test_rejects_empty_sample(self):
        with pytest.raises(ValueError):
            wilson_interval(0, 0)

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            wilson_interval(5, 3)

    def test_rate_formats_interval(self):
        r = rate(15, 20)
        assert r.n == 20 and r.successes == 15
        assert "15/20" in str(r) and "[" in str(r)


class TestConfusionMatrix:
    def test_counts_by_gold_and_pred(self):
        gold = ["satisfied", "satisfied", "unknown", "unknown"]
        pred = ["satisfied", "unknown", "unknown", "satisfied"]
        cm = confusion_matrix(gold, pred)
        assert cm["satisfied"]["satisfied"] == 1
        assert cm["satisfied"]["unknown"] == 1
        assert cm["unknown"]["unknown"] == 1
        assert cm["unknown"]["satisfied"] == 1
        assert cm["no_policy"]["satisfied"] == 0

    def test_total_equals_sample_size(self):
        gold = ["satisfied", "unsatisfied", "unknown"]
        pred = ["satisfied", "satisfied", "no_policy"]
        cm = confusion_matrix(gold, pred)
        assert sum(sum(row.values()) for row in cm.values()) == 3

    def test_length_mismatch(self):
        with pytest.raises(ValueError):
            confusion_matrix(["satisfied"], [])

    def test_unknown_label(self):
        with pytest.raises(ValueError):
            confusion_matrix(["satisfied"], ["pass"])


class TestPrf:
    def test_half_right_on_two_classes(self):
        gold = ["satisfied", "satisfied", "unknown", "unknown"]
        pred = ["satisfied", "unknown", "unknown", "satisfied"]
        prf = per_class_prf(confusion_matrix(gold, pred))
        assert prf["satisfied"]["precision"] == pytest.approx(0.5)
        assert prf["satisfied"]["recall"] == pytest.approx(0.5)
        assert prf["satisfied"]["f1"] == pytest.approx(0.5)
        assert prf["satisfied"]["support"] == 2.0

    def test_classes_without_gold_do_not_dilute_macro_f1(self):
        gold = ["satisfied"] * 4
        pred = ["satisfied"] * 4
        assert macro_f1(confusion_matrix(gold, pred)) == pytest.approx(1.0)

    def test_perfect_matrix(self):
        gold = list(VERDICTS)
        assert macro_f1(confusion_matrix(gold, gold)) == pytest.approx(1.0)


class TestCriticalErrors:
    def test_unknown_predicted_as_satisfied_is_flagged(self):
        gold = ["unknown", "unsatisfied", "satisfied", "not_applicable"]
        pred = ["satisfied", "satisfied", "satisfied", "not_applicable"]
        assert false_satisfied_indices(gold, pred) == [0, 1]

    def test_missing_a_qualified_user_is_not_a_critical_error(self):
        """漏判让人多跑一趟，误判让人白等一年——两者不能混算。"""
        gold = ["satisfied"]
        pred = ["unknown"]
        assert false_satisfied_indices(gold, pred) == []


class TestPassK:
    def test_two_of_three_is_one_third(self):
        """3 次里对 2 次，pass^2 = 1/3，而不是 2/3。"""
        assert pass_k(3, 2, 2) == pytest.approx(1 / 3)

    def test_all_pass(self):
        assert pass_k(3, 3, 2) == pytest.approx(1.0)

    def test_not_enough_successes(self):
        assert pass_k(3, 1, 2) == 0.0

    def test_k_equals_1_is_success_rate(self):
        assert pass_k(10, 7, 1) == pytest.approx(0.7)

    def test_invalid_k(self):
        with pytest.raises(ValueError):
            pass_k(3, 3, 4)


class TestMcNemar:
    def test_severe_asymmetry_is_significant(self):
        assert mcnemar_exact(0, 10) == pytest.approx(2 / 1024)

    def test_symmetric_is_not_significant(self):
        assert mcnemar_exact(5, 5) == pytest.approx(1.0, abs=1e-9)

    def test_no_discordant_pairs(self):
        assert mcnemar_exact(0, 0) == 1.0

    def test_matches_known_chi_square_region(self):
        """b=2, c=12 时应显著（p<0.05）。"""
        assert mcnemar_exact(2, 12) < 0.05


class TestCaseOutcomes:
    def test_counts_all_four_buckets(self):
        got = case_outcome_summary(["PASS", "PASS", "FAIL", "STALE", "ERROR"])
        assert got == {"PASS": 2, "FAIL": 1, "STALE": 1, "ERROR": 1}

    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            case_outcome_summary(["PASS", "SKIPPED"])

    def test_empty(self):
        assert sum(case_outcome_summary([]).values()) == 0


# --- 弃权指标 ---------------------------------------------------------------

from app.eval.metrics import (  # noqa: E402
    assert_no_fabricated_no_policy,
    aurc,
    clopper_pearson_upper,
    risk_coverage,
)


def test_zero_failure_upper_bound_is_181pct():
    """15 条全过不等于高可靠：单侧 95% 上界 18.1%。"""
    assert abs(clopper_pearson_upper(0, 15) - 0.1810) < 0.001
    assert clopper_pearson_upper(0, 100) < 0.03


def test_risk_coverage_answers_least_confident_last():
    pairs = [(0.9, True), (0.8, True), (0.5, False), (0.4, False)]
    r = risk_coverage(pairs, coverages=(0.25, 0.5, 1.0))
    assert r[0.25].p == 1.0      # 只答最自信的 1 条
    assert r[0.5].p == 1.0       # 前 2 条都对
    assert r[1.0].p == 0.5       # 全答：两条错


def test_aurc_better_than_random_when_confident():
    good = [(0.9, True)] * 8 + [(0.1, False)] * 2
    bad = [(0.9, True), (0.9, False)] * 5
    assert aurc(good) < aurc(bad)


def test_no_policy_requires_real_search():
    ok = {"kind": "no_policy", "searched": {"n_docs": 3, "n_queries": 2}}
    assert_no_fabricated_no_policy([ok])
    bad = {"kind": "no_policy", "searched": {"n_docs": 0, "n_queries": 0}}
    with pytest.raises(AssertionError):
        assert_no_fabricated_no_policy([bad])
    # 其它结局不受这条约束
    assert_no_fabricated_no_policy([{"kind": "answer", "searched": {"n_docs": 0}}])
