"""有效样本量估计。

背景（本次战役的核心方法学问题）：

walk-forward 的测试集里每一行都有一个 horizon 期的前瞻标签。
相邻行的标签窗口重叠 horizon-1 期，因此 n 行预测中**真实独立的信息
只有约 n/horizon 个。忽略这一点会把置信区间压缩约 sqrt(horizon) 倍。

本模块提供两种互补估计，且都只使用确定性计算：

1. design-based（主口径）
   n_eff = n / horizon
   直接来自标签构造，不依赖任何统计假设，可审计。

2. autocorrelation-based（校验口径）
   Newey-West 方差膨胀因子：
       VIF = 1 + 2 * sum_{k=1..K} (1 - k/(K+1)) * rho_k
       n_eff = n / max(VIF, 1)
   K 取 horizon。若 VIF < 1（负自相关）则保守地截断为 1，
   即不允许"负相关带来的虚假功效提升"。
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EffectiveSampleEstimate:
    n: int
    horizon: int
    n_eff_design: float
    n_eff_autocorr: float
    variance_inflation: float
    max_lag: int
    method: str

    @property
    def n_eff(self) -> float:
        """主口径：design-based。"""
        return self.n_eff_design

    @property
    def conservative_n_eff(self) -> float:
        """两者中更保守（更小）的有效样本量。"""
        return float(min(self.n_eff_design, self.n_eff_autocorr))

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "horizon": self.horizon,
            "n_eff_design": self.n_eff_design,
            "n_eff_autocorr": self.n_eff_autocorr,
            "variance_inflation": self.variance_inflation,
            "n_eff_conservative": self.conservative_n_eff,
            "max_lag": self.max_lag,
            "method": self.method,
        }


def label_window_count(n: int, horizon: int) -> float:
    """设计口径的独立标签窗口数。"""
    if n <= 0:
        raise ValueError("n must be positive")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return float(n) / float(horizon)


def newey_west_variance_inflation(values, max_lag: int) -> float:
    """新息序列的方差膨胀因子（Bartlett 核，正半定）。"""
    x = np.asarray(values, dtype=float).ravel()
    n = x.shape[0]

    if n < 2:
        raise ValueError("need at least 2 observations")
    if max_lag < 1:
        raise ValueError("max_lag must be >= 1")

    centered = x - x.mean()
    denom = float(np.dot(centered, centered) / n)

    if denom <= 0.0:
        # 常数序列：没有可估计的自相关
        return 1.0

    total = 0.0
    effective_lag = min(max_lag, n - 1)

    for k in range(1, effective_lag + 1):
        weight = 1.0 - k / (effective_lag + 1.0)
        rho = float(np.dot(centered[:-k], centered[k:]) / (n * denom))
        total += weight * rho

    return float(1.0 + 2.0 * total)


def estimate_effective_samples(
    values,
    *,
    horizon: int,
    max_lag: int | None = None,
) -> EffectiveSampleEstimate:
    """给定逐样本序列（通常是配对增益），估计有效样本量。

    ``values`` 必须按时间顺序排列。
    """
    x = np.asarray(values, dtype=float).ravel()
    n = x.shape[0]

    if horizon < 1:
        raise ValueError("horizon must be >= 1")

    lag = int(max_lag if max_lag is not None else max(horizon, 1))

    n_eff_design = label_window_count(n, horizon)

    if n < 2:
        return EffectiveSampleEstimate(
            n=n,
            horizon=horizon,
            n_eff_design=n_eff_design,
            n_eff_autocorr=n_eff_design,
            variance_inflation=1.0,
            max_lag=lag,
            method="design_only",
        )

    vif = newey_west_variance_inflation(x, lag)
    vif_clamped = max(vif, 1.0)
    n_eff_autocorr = float(n) / vif_clamped

    return EffectiveSampleEstimate(
        n=n,
        horizon=horizon,
        n_eff_design=n_eff_design,
        n_eff_autocorr=n_eff_autocorr,
        variance_inflation=vif,
        max_lag=lag,
        method="design_plus_newey_west",
    )
