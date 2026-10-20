
import numpy as np
from scipy.stats import t

from alpha_lab.calibration.cs_inference_calibration import (
    _hac,
    simulate_rank_ic,
)


def manual_hac(values, lag):
    n = len(values)
    mean = values.mean()
    centered = values - mean

    # 滞后 0 的自协方差
    gamma0 = np.dot(centered, centered) / n

    # 长期方差估计
    long_run_variance = gamma0

    for k in range(1, lag + 1):
        gamma_k = (
            np.dot(centered[k:], centered[:-k]) / n
        )

        # Bartlett 权重
        weight = 1 - k / (lag + 1)

        long_run_variance += 2 * weight * gamma_k

    se = np.sqrt(max(long_run_variance, 0) / n)

    df = max(1, n - lag - 1)
    critical = t.ppf(0.975, df)

    if se == 0:
        return float(mean), float(mean), 1.0, float(se)

    t_stat = mean / se
    pvalue = 2 * t.sf(abs(t_stat), df)

    low = mean - critical * se
    high = mean + critical * se

    return float(low), float(high), float(pvalue), float(se)


series = simulate_rank_ic(
    seed=20261020,
    assets=10,
    sections=250,
    rho=0.9,
    common_shock=0.0,
    heteroskedastic=False,
    horizon=1,
    effect=0.0,
)

print("Mean RankIC:", series.mean())
print("Sample size:", len(series))

for lag in (0, 4, 12, 32, 64):
    low, high, p, se = manual_hac(series, lag)

    # 和 Alpha Lab 现有实现进行对照
    ref_low, ref_high, ref_p = _hac(series, lag)

    np.testing.assert_allclose(
        [low, high, p],
        [ref_low, ref_high, ref_p],
        rtol=1e-10,
        atol=1e-12,
    )

    print(
        f"lag={lag:2d} | "
        f"SE={se:.6f} | "
        f"p={p:.4f} | "
        f"CI=[{low:.4f}, {high:.4f}]"
    )
