"""最小可检测效应（Minimum Detectable Effect, MDE）。

MDE 回答的是本次战役真正需要回答的问题：
"以当前样本量和依赖结构，**最小的、能被可靠检出的效应**是多少？"

如果观测到的增量远小于 MDE，那么"没有显著增量"这一结论
并不等于"没有增量"，而是"本次设计无法判定"。这正是
INCONCLUSIVE 这一分类存在的原因。

标准两样本/配对检验的 MDE（双侧 alpha，power = 1 - beta）：

    MDE = (z_{1-alpha/2} + z_{power}) * SE

其中 SE 使用**配对**的标准误，并且必须已经考虑标签重叠
（即 SE 不能是 sd/sqrt(n)，而应是 sd/sqrt(n_eff) 或
 bootstrap 标准差）。
"""

import numpy as np

Z_ALPHA_2_95 = 1.959963984540054
Z_POWER_80 = 0.8416212335729143


def minimum_detectable_effect(
    standard_error: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> float:
    """由标准误给出 MDE。"""
    if standard_error < 0:
        raise ValueError("standard_error must be non-negative")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not 0.0 < power < 1.0:
        raise ValueError("power must be in (0, 1)")

    if alpha == 0.05 and power == 0.80:
        multiplier = Z_ALPHA_2_95 + Z_POWER_80
    else:
        from statistics import NormalDist

        normal = NormalDist()
        multiplier = (
            normal.inv_cdf(1.0 - alpha / 2.0)
            + normal.inv_cdf(power)
        )

    return float(multiplier * standard_error)


def mde_from_standard_error(
    standard_error: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> dict:
    return {
        "mde": minimum_detectable_effect(
            standard_error, alpha=alpha, power=power
        ),
        "standard_error": float(standard_error),
        "alpha": alpha,
        "power": power,
    }


def mde_from_gain_series(
    gains,
    *,
    n_eff: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> dict:
    """设计口径的 MDE：SE = sd(gain) / sqrt(n_eff)。

    仅适用于"逐样本增益的均值"这类统计量（Δlogloss、ΔBrier）。
    ΔAUC 不是均值形式，必须用 bootstrap 标准误。
    """
    x = np.asarray(gains, dtype=float).ravel()

    if x.shape[0] < 2:
        raise ValueError("need at least 2 observations")
    if n_eff <= 0 or not np.isfinite(n_eff):
        raise ValueError("n_eff must be positive and finite")

    sd = float(x.std(ddof=1))
    standard_error = sd / float(np.sqrt(n_eff))

    return {
        **mde_from_standard_error(
            standard_error, alpha=alpha, power=power
        ),
        "sample_sd": sd,
        "n_eff": float(n_eff),
        "estimator": "sd_over_sqrt_n_eff",
    }
