"""统计功效层测试。

重点验证：
- fast_auc 与 sklearn 完全一致（含并列值）；
- 配对 block bootstrap 的配对性、确定性与块长约束；
- 有效样本量、MDE、多重检验校正的数值正确性；
- 三分类决策规则的关键分支（不允许只看 p 值）。
"""

import numpy as np
import pytest

from alpha_lab.stats import (
    INCONCLUSIVE,
    REJECTED,
    SUPPORTED,
    MetricAssessment,
    VerdictPolicy,
    adjust_pvalues,
    benjamini_hochberg,
    brier_loss_per_sample,
    classify_result,
    combine_verdicts,
    estimate_effective_samples,
    fast_auc,
    holm_bonferroni,
    label_window_count,
    logloss_per_sample,
    mde_from_gain_series,
    minimum_detectable_effect,
    paired_block_bootstrap,
)
from alpha_lab.stats.paired_bootstrap import resolve_block_size

from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def test_fast_auc_matches_sklearn_including_ties():
    rng = np.random.default_rng(7)

    for n in (64, 500, 4000):
        y = rng.integers(0, 2, n)
        p = rng.random(n)
        assert fast_auc(y, p) == pytest.approx(roc_auc_score(y, p), abs=1e-12)

    y = rng.integers(0, 2, 3000)
    p = np.round(rng.random(3000), 2)  # 大量并列
    assert fast_auc(y, p) == pytest.approx(roc_auc_score(y, p), abs=1e-12)


def test_fast_auc_degenerate_and_invalid():
    assert np.isnan(fast_auc(np.zeros(10), np.linspace(0, 1, 10)))
    with pytest.raises(ValueError):
        fast_auc(np.array([0, 1]), np.array([0.5]))
    with pytest.raises(ValueError):
        fast_auc(np.array([0, 1]), np.array([np.nan, 0.5]))


def test_pointwise_losses_match_sklearn():
    rng = np.random.default_rng(11)
    y = rng.integers(0, 2, 1000)
    p = np.clip(rng.random(1000), 1e-6, 1 - 1e-6)

    assert logloss_per_sample(y, p).mean() == pytest.approx(
        log_loss(y, p, labels=[0, 1]), abs=1e-10
    )
    assert brier_loss_per_sample(y, p).mean() == pytest.approx(
        brier_score_loss(y, p), abs=1e-10
    )


# --------------------------------------------------------------------------
# block bootstrap
# --------------------------------------------------------------------------
def test_resolve_block_size_enforces_horizon_coverage():
    assert resolve_block_size(1000, 24) == 24
    assert resolve_block_size(1000, 24, requested=72) == 72
    # 允许按需放大，但绝不允许小于 horizon
    with pytest.raises(ValueError):
        resolve_block_size(1000, 24, requested=12)
    # 块长远大于样本量时截断
    assert resolve_block_size(10, 4, requested=1000) == 10
    with pytest.raises(ValueError):
        resolve_block_size(1000, 0)


def test_paired_bootstrap_is_deterministic_and_paired():
    rng = np.random.default_rng(3)
    n = 2000
    y = rng.integers(0, 2, n)
    p_base = rng.random(n)
    p_comb = np.clip(p_base + 0.02 * (y - 0.5), 0, 1)

    first = paired_block_bootstrap(
        y, p_base, p_comb, block_size=8, n_boot=200, random_state=5
    )
    second = paired_block_bootstrap(
        y, p_base, p_comb, block_size=8, n_boot=200, random_state=5
    )

    for name in ("auc", "logloss", "brier"):
        assert first[name].point == second[name].point
        assert first[name].ci_95_lower == second[name].ci_95_lower
        assert first[name].p_two_sided == second[name].p_two_sided

    # logloss 点估计必须等于逐样本增益的均值（配对性）
    gains = logloss_per_sample(y, p_base) - logloss_per_sample(y, p_comb)
    assert first["logloss"].point == pytest.approx(gains.mean(), abs=1e-14)
    # 区间应包含点估计
    assert first["logloss"].ci_95_lower <= first["logloss"].point
    assert first["logloss"].ci_95_upper >= first["logloss"].point


def test_paired_bootstrap_full_block_has_zero_variance():
    rng = np.random.default_rng(4)
    n = 300
    y = rng.integers(0, 2, n)
    p_base = rng.random(n)
    p_comb = rng.random(n)

    result = paired_block_bootstrap(
        y, p_base, p_comb, block_size=n, n_boot=50, random_state=1
    )["logloss"]

    assert result.boot_sd == pytest.approx(0.0, abs=1e-15)
    assert result.ci_95_lower == pytest.approx(result.point, abs=1e-12)
    assert result.ci_95_upper == pytest.approx(result.point, abs=1e-12)


def test_paired_bootstrap_p_value_is_floored_not_zero():
    rng = np.random.default_rng(9)
    n = 4000
    y = rng.integers(0, 2, n)
    p_base = rng.random(n)
    p_comb = np.clip(p_base + 0.3 * (y - 0.5), 0, 1)

    result = paired_block_bootstrap(
        y, p_base, p_comb, block_size=4, n_boot=100, random_state=2
    )["auc"]

    assert result.p_two_sided >= 1.0 / 101
    assert result.ci_95_lower > 0.0


def test_centered_bootstrap_null_is_not_automatically_significant():
    rng = np.random.default_rng(20261019)
    rejected = 0
    for seed in range(30):
        y = rng.integers(0, 2, 600)
        base = np.clip(rng.normal(0.5, 0.08, 600), 0.01, 0.99)
        combined = np.clip(base + rng.normal(0, 0.01, 600), 0.01, 0.99)
        p = paired_block_bootstrap(
            y, base, combined, block_size=6, n_boot=199, random_state=seed,
            metrics=("logloss",),
        )["logloss"].p_two_sided
        rejected += p < 0.05
    assert rejected <= 5


def test_centered_bootstrap_correlated_null_monte_carlo():
    """Paired null with serially dependent labels and prediction noise."""
    rejected = {"auc": 0, "logloss": 0}
    for seed in range(60):
        rng = np.random.default_rng(seed + 20261020)
        n = 400
        rho = (0.0, 0.5, 0.9)[seed % 3]
        label_persistence = (0.5, 0.8, 0.95)[(seed // 3) % 3]
        block_size = (4, 20, 40)[(seed // 9) % 3]
        y = np.empty(n, dtype=int)
        y[0] = int(rng.integers(0, 2))
        for i in range(1, n):
            y[i] = y[i - 1] if rng.random() < label_persistence else 1 - y[i - 1]
        noise = np.empty(n)
        noise[0] = 0.0
        for i in range(1, n):
            scale = 0.01 if seed % 4 == 3 and i < n // 2 else 0.02
            noise[i] = rho * noise[i - 1] + rng.normal(0, scale * np.sqrt(1 - rho ** 2))
        base = np.clip(0.5 + noise, 0.01, 0.99)
        combined = np.clip(0.5 - noise, 0.01, 0.99)
        result = paired_block_bootstrap(
            y, base, combined, block_size=block_size, n_boot=99,
            random_state=seed, metrics=("auc", "logloss"))
        for name in rejected:
            rejected[name] += result[name].p_two_sided < 0.05
    assert rejected["auc"] <= 8
    assert rejected["logloss"] <= 8


def test_paired_bootstrap_rejects_misaligned_inputs():
    with pytest.raises(ValueError):
        paired_block_bootstrap(
            np.array([0, 1]), np.array([0.5]), np.array([0.5, 0.5]),
            block_size=1,
        )
    with pytest.raises(ValueError):
        paired_block_bootstrap(
            np.array([0, 2]), np.array([0.5, 0.5]), np.array([0.5, 0.5]),
            block_size=1,
        )
    with pytest.raises(ValueError):
        paired_block_bootstrap(
            np.array([0, 1]), np.array([0.5, 0.5]), np.array([0.5, 0.5]),
            block_size=1, metrics=("sharpe",),
        )


# --------------------------------------------------------------------------
# effective samples
# --------------------------------------------------------------------------
def test_label_window_count():
    assert label_window_count(2400, 24) == 100.0
    assert label_window_count(17540, 4) == 4385.0
    with pytest.raises(ValueError):
        label_window_count(100, 0)


def test_effective_samples_positive_dependence_shrinks_n_eff():
    rng = np.random.default_rng(13)
    # 强正自相关序列
    x = np.zeros(2000)
    for i in range(1, 2000):
        x[i] = 0.9 * x[i - 1] + rng.normal()

    estimate = estimate_effective_samples(x, horizon=24)
    assert estimate.n_eff_design == pytest.approx(2000 / 24)
    assert estimate.variance_inflation > 1.0
    assert estimate.n_eff_autocorr < 2000
    assert estimate.conservative_n_eff <= estimate.n_eff_design


def test_effective_samples_constant_series_has_unit_inflation():
    estimate = estimate_effective_samples(np.ones(500), horizon=5)
    assert estimate.variance_inflation == 1.0
    assert estimate.n_eff_autocorr == pytest.approx(500.0)


# --------------------------------------------------------------------------
# MDE
# --------------------------------------------------------------------------
def test_minimum_detectable_effect_scaling():
    assert minimum_detectable_effect(0.0) == 0.0
    base = minimum_detectable_effect(0.01)
    assert base == pytest.approx((1.959963984540054 + 0.8416212335729143) * 0.01)
    assert minimum_detectable_effect(0.02) == pytest.approx(2 * base)
    # 更高功效 -> 更大的 MDE
    assert minimum_detectable_effect(0.01, power=0.95) > base
    with pytest.raises(ValueError):
        minimum_detectable_effect(-1.0)


def test_mde_from_gain_series_uses_effective_sample_size():
    rng = np.random.default_rng(17)
    gains = rng.normal(0.001, 0.05, 5000)

    small = mde_from_gain_series(gains, n_eff=100)
    large = mde_from_gain_series(gains, n_eff=10000)

    assert small["mde"] > large["mde"]
    assert small["sample_sd"] == pytest.approx(gains.std(ddof=1))
    with pytest.raises(ValueError):
        mde_from_gain_series(gains, n_eff=0)


# --------------------------------------------------------------------------
# multiple testing
# --------------------------------------------------------------------------
def test_holm_and_benjamini_hochberg_known_values():
    p = [0.01, 0.2, 0.6, 0.03]

    assert holm_bonferroni(p) == pytest.approx([0.04, 0.4, 0.6, 0.09])
    assert benjamini_hochberg(p) == pytest.approx([0.04, 0.2666666667, 0.6, 0.06])

    adjusted = adjust_pvalues(p)
    assert adjusted["family_size"] == 4
    assert adjusted["raw"] == pytest.approx(p)


def test_adjustments_are_monotone_in_rank_and_never_below_raw():
    p = [0.001, 0.02, 0.2, 0.5, 0.9]

    for adjusted in (holm_bonferroni(p), benjamini_hochberg(p)):
        assert np.all(adjusted >= np.asarray(p) - 1e-12)
        assert np.all(adjusted <= 1.0)
        order = np.argsort(p)
        assert np.all(np.diff(adjusted[order]) >= -1e-12)


def test_adjust_pvalues_rejects_invalid():
    with pytest.raises(ValueError):
        adjust_pvalues([])
    with pytest.raises(ValueError):
        adjust_pvalues([0.1, 1.5])
    with pytest.raises(ValueError):
        adjust_pvalues([0.1, np.nan])


def test_two_sided_binomial_p_is_symmetric_in_both_tails():
    """回归测试：少数侧的符号也必须得到小 p。

    早期实现只对 p=0.5 计算 P(X >= k)，当 k 落在少数侧时
    （例如 36 个月里只有 7 个月为负）会错误地返回 p ≈ 1，
    从而把"方向被强烈确认"误判成"方向不可判定"。
    """
    from alpha_lab.stats.multiple_testing import two_sided_binomial_p

    # 32/36 为负
    assert two_sided_binomial_p(32, 36) < 1e-5
    # 7/36 为负 == 29/36 为正 -> 同一侧的小 p
    assert two_sided_binomial_p(7, 36) < 1e-3
    # 对称性
    assert two_sided_binomial_p(7, 36) == pytest.approx(
        two_sided_binomial_p(29, 36)
    )
    # 恰好一半 -> p 接近 1
    assert two_sided_binomial_p(18, 36) == pytest.approx(1.0)
    assert two_sided_binomial_p(17, 36) > 0.5
    # 全部落在同一侧
    assert two_sided_binomial_p(0, 6) == pytest.approx(2 / 64)
    assert two_sided_binomial_p(6, 6) == pytest.approx(2 / 64)

    with pytest.raises(ValueError):
        two_sided_binomial_p(5, 0)
    with pytest.raises(ValueError):
        two_sided_binomial_p(7, 6)


# --------------------------------------------------------------------------
# verdict
# --------------------------------------------------------------------------
def _assessment(metric, point, low, high, mde, threshold, p=0.01,
                adjusted_p=0.01):
    return MetricAssessment(
        metric=metric,
        point=point,
        ci_lower=low,
        ci_upper=high,
        mde=mde,
        practical_threshold=threshold,
        adjusted_p=adjusted_p,
        p_two_sided=p,
    )


def test_verdict_supported_requires_effect_design_and_significance():
    strong = {
        "auc": _assessment("auc", 0.01, 0.006, 0.014, mde=0.003,
                           threshold=0.005, adjusted_p=0.004),
    }
    verdict = classify_result(
        strong, primary_metric="auc", stability_ratio=0.8, max_abs_spearman=0.2
    )
    assert verdict.label == SUPPORTED


def test_verdict_underpowered_is_inconclusive_even_if_p_is_small():
    """核心：p 值小但设计不足 -> 必须 INCONCLUSIVE。"""
    weak = {
        "auc": _assessment("auc", 0.004, 0.003, 0.005, mde=0.02,
                           threshold=0.005, adjusted_p=0.0001),
    }
    verdict = classify_result(
        weak, primary_metric="auc", stability_ratio=0.9, max_abs_spearman=0.1
    )
    assert verdict.label == INCONCLUSIVE
    assert "UNDERPOWERED_FOR_PRACTICAL_EFFECT" in verdict.reasons


def test_verdict_rejects_underpowered_but_rules_out_practical_if_powered():
    powered_null = {
        "auc": _assessment("auc", 0.0001, -0.0004, 0.0006, mde=0.001,
                           threshold=0.005, adjusted_p=0.9),
    }
    verdict = classify_result(
        powered_null, primary_metric="auc", stability_ratio=0.9,
        max_abs_spearman=0.1,
    )
    assert verdict.label == REJECTED
    assert "POWERED_AND_RULES_OUT_PRACTICAL_EFFECT" in verdict.reasons


def test_verdict_rejects_confidently_harmful_increment():
    harmful = {
        "auc": _assessment("auc", -0.01, -0.014, -0.006, mde=0.003,
                           threshold=0.005, adjusted_p=0.004),
    }
    verdict = classify_result(
        harmful, primary_metric="auc", stability_ratio=0.9, max_abs_spearman=0.1
    )
    assert verdict.label == REJECTED
    assert "CONFIDENT_HARMFUL_INCREMENT" in verdict.reasons


def test_verdict_redundancy_and_instability_block_support():
    strong = {
        "auc": _assessment("auc", 0.01, 0.006, 0.014, mde=0.003,
                           threshold=0.005, adjusted_p=0.004),
    }
    redundant = classify_result(
        strong, primary_metric="auc", stability_ratio=0.9, max_abs_spearman=0.95
    )
    assert redundant.label == INCONCLUSIVE
    assert "REDUNDANT_WITH_BASELINE" in redundant.reasons

    unstable = classify_result(
        strong, primary_metric="auc", stability_ratio=0.2, max_abs_spearman=0.1
    )
    assert unstable.label == INCONCLUSIVE
    assert "UNSTABLE_ACROSS_FOLDS" in unstable.reasons


def test_verdict_multiple_testing_can_block_support():
    strong = {
        "auc": _assessment("auc", 0.01, 0.006, 0.014, mde=0.003,
                           threshold=0.005, adjusted_p=0.4),
    }
    verdict = classify_result(
        strong, primary_metric="auc", stability_ratio=0.8, max_abs_spearman=0.2
    )
    assert verdict.label == INCONCLUSIVE
    assert "NOT_SIGNIFICANT_AFTER_MULTIPLE_TESTING" in verdict.reasons


def test_verdict_missing_primary_metric_raises():
    with pytest.raises(ValueError):
        classify_result({}, primary_metric="auc")


def test_combine_verdicts_two_axes():
    assert combine_verdicts(
        {"label": SUPPORTED, "reasons": []},
        {"label": INCONCLUSIVE, "reasons": []},
    )["label"] == SUPPORTED

    # 机制被否证 -> 整体 REJECTED，即使增量支持
    assert combine_verdicts(
        {"label": SUPPORTED, "reasons": []},
        {"label": REJECTED, "reasons": []},
    )["label"] == REJECTED

    assert combine_verdicts(
        {"label": INCONCLUSIVE, "reasons": []},
        {"label": INCONCLUSIVE, "reasons": []},
    )["label"] == INCONCLUSIVE


def test_policy_threshold_defaults_are_declared():
    policy = VerdictPolicy()
    assert policy.threshold_for("auc") == 0.005
    assert policy.threshold_for("logloss") == 0.0005
    assert policy.threshold_for("unknown") == 0.0
    assert policy.min_stability_ratio == 0.5
