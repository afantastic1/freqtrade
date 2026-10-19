"""统计方法自检（Q1–Q4）的单元测试。全部使用合成数据。"""

import numpy as np
import pytest

from alpha_lab.stats.selfcheck import (
    _placebo_blocks_for_run,
    assessments_from_audit,
    family_sensitivity,
    neff_mde_consistency,
    primary_metric_sensitivity,
)
from alpha_lab.stats.verdict import (
    VerdictPolicy,
    classify_result,
    combine_verdicts,
)


def make_run(
    run_id: str,
    *,
    auc_point: float,
    auc_ci: tuple[float, float],
    auc_mde: float,
    auc_p: float,
    logloss_point: float = 0.0,
    logloss_ci: tuple[float, float] = (-0.01, 0.01),
    logloss_mde: float = 0.1,
    brier_point: float = 0.0,
    brier_ci: tuple[float, float] = (-0.005, 0.005),
    brier_mde: float = 0.05,
    mechanism: str = "INCONCLUSIVE",
    stability: float = 0.6,
    rho: float = 0.2,
    adjusted_p: float | None = None,
) -> dict:
    """构造一条与审计重评结构一致的 run 记录。

    `verdict` / `overall_verdict` 用与生产同一套判定函数**现场算出**，
    以免测试里出现"人为写死的 recorded 标签"。
    """
    policy = VerdictPolicy()
    adjusted = auc_p if adjusted_p is None else adjusted_p

    def metric(name, point, ci, mde):
        return {
            "metric": name,
            "point": point,
            "ci_95_lower": ci[0],
            "ci_95_upper": ci[1],
            "mde": mde,
            "practical_threshold": policy.threshold_for(name),
            "adjusted_p": adjusted if name == "auc" else None,
            "p_two_sided": auc_p if name == "auc" else 0.5,
        }

    run = {
        "run_id": run_id,
        "timeframe": "1h",
        "horizon": 4,
        "n": 1000,
        "n_eff": {
            "design": 250.0,
            "autocorr": 300.0,
            "variance_inflation": 3.3,
            "max_lag": 1,
        },
        "metrics": {
            "auc": metric("auc", auc_point, auc_ci, auc_mde),
            "logloss": metric("logloss", logloss_point, logloss_ci, logloss_mde),
            "brier": metric("brier", brier_point, brier_ci, brier_mde),
        },
        "stability": {"positive_ratio": stability},
        "redundancy": {"max_abs_spearman": rho},
        "mechanism_verdict": {"label": mechanism, "reasons": []},
    }

    incremental = classify_result(
        assessments_from_audit(run, policy),
        primary_metric="auc",
        stability_ratio=stability,
        max_abs_spearman=rho,
        policy=policy,
    )
    run["verdict"] = incremental.as_dict()
    run["overall_verdict"] = combine_verdicts(
        incremental, run["mechanism_verdict"]
    )
    return run


def supported_run(run_id: str = "strong") -> dict:
    return make_run(
        run_id,
        auc_point=0.01,
        auc_ci=(0.006, 0.014),
        auc_mde=0.003,
        auc_p=0.001,
        adjusted_p=0.003,
        mechanism="SUPPORTED",
    )


def test_assessments_from_audit_uses_policy_thresholds():
    run = supported_run()
    assessments = assessments_from_audit(run, VerdictPolicy())
    assert set(assessments) == {"auc", "logloss", "brier"}
    assert assessments["auc"].practical_threshold == 0.005
    assert assessments["auc"].point == 0.01


def test_supported_run_is_actually_supported():
    run = supported_run()
    assert run["verdict"]["label"] == "SUPPORTED"
    assert run["overall_verdict"]["label"] == "SUPPORTED"


def test_primary_metric_sensitivity_flags_no_self_serving_flip():
    # auc 支持；logloss/brier 的 MDE 远大于各自阈值 -> 不可能 SUPPORTED。
    runs = [supported_run("a"), supported_run("b")]
    result = primary_metric_sensitivity(runs, VerdictPolicy())
    assert result["recorded_primary_metric"] == "auc"
    assert result["any_self_serving_flip"] is False
    assert result["variants"]["auc"]["n_flips_vs_recorded"] == 0


def test_primary_metric_sensitivity_detects_self_serving_flip():
    # auc 不可分辨（INCONCLUSIVE），brier 恰好可分辨为正 ->
    # 换成 brier 主指标就变成 SUPPORTED，属于"只有某个主指标才支持"。
    policy = VerdictPolicy()
    base = make_run(
        "flip",
        auc_point=0.001,
        auc_ci=(-0.004, 0.006),
        auc_mde=0.02,  # MDE > 阈值 -> design 不足
        auc_p=0.4,
        mechanism="SUPPORTED",
        adjusted_p=0.01,
    )
    # 只把 brier 改成"可分辨为正"，并给它一个显著的 adjusted_p
    base["metrics"]["brier"].update(
        {
            "point": 0.001,
            "ci_95_lower": 0.0004,
            "ci_95_upper": 0.0016,
            "mde": 0.0001,
            "adjusted_p": 0.01,
        }
    )
    assert base["verdict"]["label"] == "INCONCLUSIVE"

    result = primary_metric_sensitivity([base], policy)
    assert result["any_self_serving_flip"] is True
    assert any(
        flip["variant"] == "brier" for flip in result["self_serving_flips"]
    )


def test_family_sensitivity_strong_result_invariant_to_family():
    runs = [
        supported_run("strong"),
        make_run(
            "weak",
            auc_point=0.004,
            auc_ci=(-0.001, 0.009),
            auc_mde=0.01,
            auc_p=0.2,
        ),
        make_run(
            "null",
            auc_point=0.0,
            auc_ci=(-0.01, 0.01),
            auc_mde=0.02,
            auc_p=0.9,
        ),
    ]
    variants = {
        "all_three": ["strong", "weak", "null"],
        "strong_only": ["strong"],
        "strong_and_weak": ["strong", "weak"],
    }
    result = family_sensitivity(runs, variants, VerdictPolicy())
    for name in variants:
        labels = result[name]["labels"]
        assert labels["strong"]["incremental"] == "SUPPORTED"
    # BH 随族变大而变保守，但不能把最强的那个打成非显著
    assert result["all_three"]["labels"]["strong"]["adjusted_p"] <= 0.05
    assert (
        result["all_three"]["labels"]["strong"]["adjusted_p"]
        >= result["strong_only"]["labels"]["strong"]["adjusted_p"]
    )


def test_neff_mde_consistency_reports_scaling():
    runs = [supported_run("a")]
    result = neff_mde_consistency(runs)
    row = result["rows"][0]
    assert row["n_eff_design_n_over_h"] == pytest.approx(250.0)
    assert row["mde_scaling_if_full_window_planning"] == pytest.approx(
        np.sqrt(26304 / 1000), rel=1e-6
    )
    assert "n_eff" in result["canonical_rule"]


def test_placebo_blocks_reports_fpr_fields():
    rng = np.random.default_rng(0)
    n = 3000
    # 自相关标签：重叠标签依赖的最简模型
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = 0.6 * y[i - 1] + rng.normal(0.0, 0.5)
    y = (y > 0).astype(float)
    p_base = rng.uniform(0.3, 0.7, n)
    p_comb = p_base + rng.normal(0.0, 0.01, n)

    result = _placebo_blocks_for_run(
        y, p_base, p_comb,
        horizon=4, n_shift=25, n_boot=60, random_state=1,
    )
    assert set(result) == {"one", "horizon"}
    for item in result.values():
        assert item["n_shift"] == 25
        assert 0.0 <= item["fpr_ci_excludes_zero"] <= 1.0
        assert 0.0 <= item["fpr_p_lt_0.05"] <= 1.0
    assert result["one"]["block_size"] == 1
    assert result["horizon"]["block_size"] == 4
    # 纯噪声候选 + block=horizon：假阳性率应该很低（宽松上界）
    assert result["horizon"]["fpr_ci_excludes_zero"] <= 0.25
