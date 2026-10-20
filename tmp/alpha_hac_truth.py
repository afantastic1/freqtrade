
import numpy as np
from scipy.stats import t

from alpha_lab.calibration.cs_inference_calibration import (
    _hac,
    wilson_interval,
)

REPETITIONS = 300
LAGS = [0, 12, 32]
SAMPLE_SIZES = [80, 250, 1000]


def true_mean_variance(rho, n):
    """平稳 AR(1) 样本均值的精确有限样本方差。"""
    gamma0 = 1.0 / (1 - rho**2)
    k = np.arange(1, n)

    correction = 1 + 2 * np.sum(
        (1 - k / n) * rho**k
    )

    return gamma0 * correction / n


def simulate_ar1(rng, rho, n):
    x = np.empty(n)

    # 从平稳分布初始化
    x[0] = rng.normal(
        scale=1 / np.sqrt(1 - rho**2)
    )

    eps = rng.normal(size=n - 1)

    for i in range(1, n):
        x[i] = rho * x[i - 1] + eps[i - 1]

    return x


def experiment(rho, n):
    true_var = true_mean_variance(rho, n)
    true_se = np.sqrt(true_var)

    rng = np.random.default_rng(
        20261020 + 10000 * n + int(rho * 100)
    )

    results = {
        lag: {"se2": [], "reject": 0}
        for lag in LAGS
    }

    means = []

    for _ in range(REPETITIONS):
        x = simulate_ar1(rng, rho, n)
        means.append(x.mean())

        for lag in LAGS:
            low, high, p = _hac(x, lag)

            df = max(1, n - lag - 1)
            critical = t.ppf(0.975, df)

            # 从 HAC 置信区间反推出估计标准误
            estimated_se = (
                (high - low) / (2 * critical)
            )

            results[lag]["se2"].append(
                estimated_se**2
            )
            results[lag]["reject"] += int(
                p < 0.05
            )

    empirical_se = np.std(means, ddof=1)

    print(f"\n=== rho={rho}, T={n} ===")
    print(f"True SE:      {true_se:.6f}")
    print(f"Empirical SE: {empirical_se:.6f}")
    print("Lag | E[SE²]/TrueVar | Type-I | Wilson95%")

    for lag in LAGS:
        result = results[lag]

        ratio = (
            np.mean(result["se2"]) / true_var
        )
        errors = result["reject"]
        rate = errors / REPETITIONS
        ci = wilson_interval(
            errors, REPETITIONS
        )

        print(
            f"{lag:3d} | "
            f"{ratio:14.3f} | "
            f"{rate:6.2%} | "
            f"[{ci[0]:.2%}, {ci[1]:.2%}]"
        )


for rho in (0.0, 0.5, 0.9):
    for n in SAMPLE_SIZES:
        experiment(rho, n)
