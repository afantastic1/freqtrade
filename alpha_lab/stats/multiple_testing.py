"""多重检验校正。

本战役在**同一份开发数据**上做了 14 次增量评价。
不做校正地把"某一个 run 的 CI 不包含 0"当作发现，
等价于选择性推断。

提供两种口径：

- Holm–Bonferroni：控制族错误率（FWER）。
- Benjamini–Hochberg：控制错误发现率（FDR）。

诚实声明：这些 run 共用同一段测试区间、同一个基线模型，
彼此高度相关，因此上述校正是**近似**的（真实有效自由度
小于 run 数）。校正后的 p 值应与 MDE 一起解读，而不是单独使用。
"""

from math import comb

import numpy as np


def two_sided_binomial_p(successes: int, trials: int, p: float = 0.5) -> float:
    """精确二项检验的双侧 p 值（非 p=0.5 时取两侧较小尾 ×2）。

    用于"逐月符号是否显著偏离随机"这类检验 —— 它只看符号，
    不依赖误差分布假设，因此适合作为方向证据的保守口径。
    """
    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0 <= successes <= trials:
        raise ValueError("successes out of range")
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")

    def tail(k: int) -> float:
        return sum(
            comb(trials, i) * (p ** i) * ((1.0 - p) ** (trials - i))
            for i in range(k, trials + 1)
        )

    # 双侧 p = 2 × 较小的那一尾。注意不能对 p=0.5 走捷径只算
    # P(X >= k)：当 k 落在少数侧时（例如 7/36 个月为负），
    # P(X >= k) 接近 1，会错误地得到 p ≈ 1。
    lower = sum(
        comb(trials, i) * (p ** i) * ((1.0 - p) ** (trials - i))
        for i in range(0, successes + 1)
    )
    upper = tail(successes)

    return float(min(1.0, 2.0 * min(lower, upper)))


def _validate(pvalues):
    p = np.asarray(pvalues, dtype=float).ravel()

    if p.size == 0:
        raise ValueError("pvalues must not be empty")
    if not np.isfinite(p).all():
        raise ValueError("pvalues must be finite")
    if ((p < 0.0) | (p > 1.0)).any():
        raise ValueError("pvalues must be in [0, 1]")

    return p


def holm_bonferroni(pvalues) -> np.ndarray:
    """Holm–Bonferroni 逐步下降的调整 p 值（FWER）。"""
    p = _validate(pvalues)
    m = p.size

    order = np.argsort(p, kind="mergesort")
    ranked = p[order]

    # 单调性：累积最大值，并保证不超过 1
    adjusted_sorted = np.maximum.accumulate(ranked * (m - np.arange(m)))
    adjusted_sorted = np.clip(adjusted_sorted, 0.0, 1.0)

    adjusted = np.empty(m, dtype=float)
    adjusted[order] = adjusted_sorted
    return adjusted


def benjamini_hochberg(pvalues) -> np.ndarray:
    """Benjamini–Hochberg 调整 p 值（FDR）。"""
    p = _validate(pvalues)
    m = p.size

    order = np.argsort(p, kind="mergesort")
    ranked = p[order]

    # 反向累积最小值
    scaled = ranked * m / np.arange(1, m + 1)
    reversed_min = np.minimum.accumulate(scaled[::-1])[::-1]
    adjusted_sorted = np.clip(reversed_min, 0.0, 1.0)

    adjusted = np.empty(m, dtype=float)
    adjusted[order] = adjusted_sorted
    return adjusted


def adjust_pvalues(pvalues) -> dict:
    """同时给出两种校正结果，便于审计对照。"""
    p = _validate(pvalues)

    holm = holm_bonferroni(p)
    bh = benjamini_hochberg(p)

    return {
        "family_size": int(p.size),
        "raw": [float(v) for v in p],
        "holm_bonferroni": [float(v) for v in holm],
        "benjamini_hochberg": [float(v) for v in bh],
    }
