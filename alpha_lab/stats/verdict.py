"""SUPPORTED / REJECTED / INCONCLUSIVE 三分类决策规则。

设计原则（对应"不允许仅根据 p 值或单次 AUC 决策"）：

1. **p 值只是必要条件。** SUPPORTED 需要同时满足：主指标"可分辨为正"
   （区间下界 > 0 且点估计 >= 实际意义阈值）、主指标设计功效充足
   （MDE <= 实际意义阈值）、多重检验校正后显著、跨折稳定、与基线不冗余、
   预注册机制方向未被否证、且没有任何指标"可分辨为有害"。

2. **设计不足时只能给 INCONCLUSIVE。** 若 MDE 大于实际意义阈值，本次设计
   无法判定该量级的效应，此时无论 p 值多大都不能给 REJECTED。

3. **REJECTED 有两种正当来源：**
   (a) 某个指标被可分辨地证明为**有害**增量（区间上界 < 0 且幅度 >= 阈值）；
   (b) 主指标设计功效充足，且区间完全排除了有实际意义的正效应。

4. 冗余与跨折不稳定一律 INCONCLUSIVE：它们让"独立增量"不可识别。

5. **机制方向与增量价值是两个轴。** logistic 模型自行学习系数符号，因此
   ΔAUC / Δlogloss 对因子整体取反不变。"机制方向被否证"与"存在独立增量"
   可以同时成立。`classify_result` 判定**增量价值**；机制方向由
   `mechanism_verdict`（见 audit 模块）单独判定；`combine_verdicts`
   给出二者合并后的最终标签。
"""

from dataclasses import dataclass, field

DEFAULT_PRACTICAL_THRESHOLDS = {
    # 单位与各自指标一致。取值依据见统计审计报告的"实际意义阈值"一节。
    "auc": 0.005,
    "logloss": 0.0005,
    "brier": 0.00025,
}

SUPPORTED = "SUPPORTED"
REJECTED = "REJECTED"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class VerdictPolicy:
    alpha: float = 0.05
    power: float = 0.80
    redundancy_threshold: float = 0.7
    # 0.5 = 多数符号一致。这不是临时决定：本战役多数候选自己在 hypothesis
    # 的 F 判据里写的就是 "positive_months/total_months < 0.5 -> unstable"
    # （vol_asym F4、long_scale F3、variance_ratio F3）。少数候选写了 0.6，
    # 因此审计报告同时给出 0.6 的敏感性。
    min_stability_ratio: float = 0.5
    practical_thresholds: dict = field(
        default_factory=lambda: dict(DEFAULT_PRACTICAL_THRESHOLDS)
    )
    require_p_adjusted: bool = True
    # 严格模式：要求所有指标同时可分辨为正。在当前数据尺度下
    # Δlogloss/ΔBrier 的 MDE 远大于实际意义阈值，因此该模式下不会
    # 出现 SUPPORTED —— 这正是"设计不足时只能 INCONCLUSIVE"的体现。
    require_concordant_metrics: bool = False

    def threshold_for(self, metric: str) -> float:
        return float(self.practical_thresholds.get(metric, 0.0))


@dataclass(frozen=True)
class MetricAssessment:
    """单个指标的增量证据。正值代表组合模型优于基线。"""

    metric: str
    point: float
    ci_lower: float
    ci_upper: float
    mde: float
    practical_threshold: float
    adjusted_p: float | None = None
    p_two_sided: float | None = None

    @property
    def design_adequate(self) -> bool:
        """本次设计的 MDE 是否能覆盖"有实际意义"的效应量。"""
        if self.practical_threshold <= 0:
            return False
        return self.mde <= self.practical_threshold

    @property
    def resolvable_positive(self) -> bool:
        """区间下界 > 0，且点估计达到实际意义阈值。"""
        return (
            self.ci_lower > 0.0
            and self.point >= self.practical_threshold
        )

    @property
    def confident_harmful(self) -> bool:
        """可分辨地为负增量：区间上界 < 0，且幅度达到实际意义阈值。"""
        return (
            self.ci_upper < 0.0
            and (-self.point) >= self.practical_threshold
        )

    @property
    def exceeds_mde(self) -> bool:
        return self.point >= self.mde

    @property
    def rules_out_practical(self) -> bool:
        """设计功效充分时，区间是否排除了有实际意义的正效应。"""
        return (
            self.design_adequate
            and self.ci_upper < self.practical_threshold
        )

    @property
    def mde_vs_threshold(self) -> float:
        if self.practical_threshold <= 0:
            return float("inf")
        return self.mde / self.practical_threshold

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "point": self.point,
            "ci_95_lower": self.ci_lower,
            "ci_95_upper": self.ci_upper,
            "mde": self.mde,
            "practical_threshold": self.practical_threshold,
            "mde_over_threshold": self.mde_vs_threshold,
            "adjusted_p": self.adjusted_p,
            "p_two_sided": self.p_two_sided,
            "design_adequate": self.design_adequate,
            "exceeds_mde": self.exceeds_mde,
            "resolvable_positive": self.resolvable_positive,
            "confident_harmful": self.confident_harmful,
            "rules_out_practical": self.rules_out_practical,
        }


@dataclass(frozen=True)
class Verdict:
    label: str
    reasons: tuple[str, ...]
    primary_metric: str
    detail: dict

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "reasons": list(self.reasons),
            "primary_metric": self.primary_metric,
            "detail": self.detail,
        }


def classify_result(
    assessments: dict[str, MetricAssessment],
    *,
    primary_metric: str,
    stability_ratio: float | None = None,
    max_abs_spearman: float | None = None,
    policy: VerdictPolicy | None = None,
) -> Verdict:
    """判定**增量价值**（不含机制方向；机制由 mechanism_verdict 判定）。

    注意：本判定天然是**方向无关**的 —— logistic 模型自行学习系数符号，
    因此 ΔAUC / Δlogloss 对因子整体取反不变。预注册符号方向只在机制轴使用。
    """
    policy = policy or VerdictPolicy()

    if primary_metric not in assessments:
        raise ValueError(f"missing primary metric: {primary_metric}")
    if not assessments:
        raise ValueError("no assessments given")

    items = list(assessments.values())
    primary = assessments[primary_metric]
    reasons: list[str] = []

    detail = {
        "policy": {
            "alpha": policy.alpha,
            "power": policy.power,
            "redundancy_threshold": policy.redundancy_threshold,
            "min_stability_ratio": policy.min_stability_ratio,
            "require_p_adjusted": policy.require_p_adjusted,
            "require_concordant_metrics": policy.require_concordant_metrics,
        },
        "assessments": {name: item.as_dict() for name, item in assessments.items()},
        "stability_ratio": stability_ratio,
        "max_abs_spearman": max_abs_spearman,
    }

    redundant = (
        max_abs_spearman is not None
        and max_abs_spearman >= policy.redundancy_threshold
    )
    unstable = (
        stability_ratio is not None
        and stability_ratio < policy.min_stability_ratio
    )

    all_resolvable = all(item.resolvable_positive for item in items)
    any_resolvable = any(item.resolvable_positive for item in items)
    any_harmful = any(item.confident_harmful for item in items)

    significance_ok = True
    if policy.require_p_adjusted:
        significance_ok = (
            primary.adjusted_p is not None
            and primary.adjusted_p < policy.alpha
        )

    concordance_ok = (
        all_resolvable if policy.require_concordant_metrics else True
    )

    # --- 1. 某个指标被可分辨地证明为有害 -> REJECTED ---
    if any_harmful:
        reasons.append("CONFIDENT_HARMFUL_INCREMENT")
        return Verdict(REJECTED, tuple(reasons), primary_metric, detail)

    # --- 2. 可分辨为正 + 全部门槛 -> SUPPORTED ---
    if (
        primary.resolvable_positive
        and primary.design_adequate
        and significance_ok
        and concordance_ok
        and not redundant
        and not unstable
    ):
        reasons.append("RESOLVABLE_POSITIVE_INCREMENT")
        return Verdict(SUPPORTED, tuple(reasons), primary_metric, detail)

    # --- 3. 功效充足且排除了有实际意义的正效应 -> REJECTED ---
    if primary.rules_out_practical and not any_resolvable:
        reasons.append("POWERED_AND_RULES_OUT_PRACTICAL_EFFECT")
        return Verdict(REJECTED, tuple(reasons), primary_metric, detail)

    # --- 4. 其余：无法判定（列出全部未达标的门槛）---
    if redundant:
        reasons.append("REDUNDANT_WITH_BASELINE")
    if unstable:
        reasons.append("UNSTABLE_ACROSS_FOLDS")
    if not primary.design_adequate:
        reasons.append("UNDERPOWERED_FOR_PRACTICAL_EFFECT")
    if not significance_ok:
        reasons.append("NOT_SIGNIFICANT_AFTER_MULTIPLE_TESTING")
    if policy.require_concordant_metrics and any_resolvable and not all_resolvable:
        reasons.append("METRIC_DISAGREEMENT")
    if not reasons:
        reasons.append("EVIDENCE_INCONCLUSIVE")

    return Verdict(INCONCLUSIVE, tuple(reasons), primary_metric, detail)


def _as_mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    return {"label": value.label, "reasons": list(value.reasons)}


def combine_verdicts(
    incremental, mechanism
) -> dict:
    """把"增量价值"与"机制方向"两个轴合并成最终标签。

    规则：
    - 任一轴 REJECTED -> 整体 REJECTED（假设在至少一个必要意义上被否证）；
    - 增量 SUPPORTED 且机制未被否证 -> 整体 SUPPORTED；
    - 其余 -> INCONCLUSIVE。
    """
    incremental = _as_mapping(incremental)
    mechanism = _as_mapping(mechanism)

    inc = incremental["label"]
    mech = mechanism["label"]

    if inc == REJECTED or mech == REJECTED:
        label = REJECTED
    elif inc == SUPPORTED:
        label = SUPPORTED
    else:
        label = INCONCLUSIVE

    reasons = [f"INCREMENTAL:{inc}"] + [
        f"INCREMENTAL:{reason}" for reason in incremental["reasons"]
    ]
    reasons += [f"MECHANISM:{mech}"] + [
        f"MECHANISM:{reason}" for reason in mechanism["reasons"]
    ]

    return {"label": label, "reasons": reasons}
