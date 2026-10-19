"""统计功效评估层。

本包只做确定性的数值计算，不做研究决策，也不修改任何已有记录。

设计目标：
- 显式处理标签重叠（overlapping labels）与时间序列依赖；
- 报告有效样本量、最小可检测效应（MDE）与多重检验校正；
- 支持 SUPPORTED / REJECTED / INCONCLUSIVE 三分类，且不依赖单一 p 值。
"""

from alpha_lab.stats.effective_samples import (
    EffectiveSampleEstimate,
    estimate_effective_samples,
    label_window_count,
)
from alpha_lab.stats.metrics import (
    brier_loss_per_sample,
    fast_auc,
    logloss_per_sample,
)
from alpha_lab.stats.multiple_testing import (
    adjust_pvalues,
    benjamini_hochberg,
    holm_bonferroni,
)
from alpha_lab.stats.paired_bootstrap import (
    BootstrapResult,
    paired_block_bootstrap,
)
from alpha_lab.stats.power import (
    minimum_detectable_effect,
    mde_from_gain_series,
    mde_from_standard_error,
)
from alpha_lab.stats.verdict import (
    DEFAULT_PRACTICAL_THRESHOLDS,
    INCONCLUSIVE,
    REJECTED,
    SUPPORTED,
    MetricAssessment,
    Verdict,
    VerdictPolicy,
    classify_result,
    combine_verdicts,
)

__all__ = [
    "DEFAULT_PRACTICAL_THRESHOLDS",
    "INCONCLUSIVE",
    "REJECTED",
    "SUPPORTED",
    "EffectiveSampleEstimate",
    "BootstrapResult",
    "MetricAssessment",
    "Verdict",
    "VerdictPolicy",
    "adjust_pvalues",
    "benjamini_hochberg",
    "brier_loss_per_sample",
    "classify_result",
    "combine_verdicts",
    "estimate_effective_samples",
    "fast_auc",
    "holm_bonferroni",
    "label_window_count",
    "logloss_per_sample",
    "mde_from_gain_series",
    "mde_from_standard_error",
    "minimum_detectable_effect",
    "paired_block_bootstrap",
]
