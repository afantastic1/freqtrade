
import numpy as np
from scipy.stats import norm


def long_run_variance(x, lag):
    """Bartlett HAC 长期方差估计。"""
    x = np.asarray(x, dtype=float)
    n = len(x)
    centered = x - x.mean()

    omega = np.dot(centered, centered) / n

    for k in range(1, min(lag, n - 1) + 1):
        gamma = (
            np.dot(centered[k:], centered[:-k]) / n
        )
        weight = 1 - k / (lag + 1)
        omega += 2 * weight * gamma

    return float(omega)


def ordinary_hac_se(x, lag):
    omega = long_run_variance(x, lag)

    if omega <= 0:
        return np.nan

    return float(np.sqrt(omega / len(x)))


def prewhitened_hac_se(x, residual_lag=4):
    """AR(1) 预白化后，再对残差进行 HAC。"""
    x = np.asarray(x, dtype=float)
    n = len(x)

    previous = x[:-1]
    current = x[1:]

    # 使用带截距 OLS 估计 AR(1)
    a = previous - previous.mean()
    b = current - current.mean()

    denominator = np.dot(a, a)
    if denominator <= 0:
        return np.nan, np.nan

    phi = float(np.dot(a, b) / denominator)
    intercept = current.mean() - phi * previous.mean()

    # AR(1) 残差
    residuals = current - (
        intercept + phi * previous
    )

    # 不稳定的 AR(1) 估计不直接输出有效推断
    if abs(phi) >= 1:
        return np.nan, phi

    omega_residual = long_run_variance(
        residuals, residual_lag
    )

    if omega_residual <= 0:
        return np.nan, phi

    # 将残差长期方差还原到原始序列
    omega_original = (
        omega_residual / (1 - phi) ** 2
    )

    se = np.sqrt(omega_original / n)

    return float(se), phi


def simulate_ar1(rng, rho, n):
    x = np.empty(n)

    # 从平稳分布初始化
    x[0] = rng.normal(
        scale=1 / np.sqrt(1 - rho**2)
    )

    innovations = rng.normal(size=n - 1)

    for i in range(1, n):
        x[i] = rho * x[i - 1] + innovations[i - 1]

    return x


def run_experiment(rho, n, repetitions=300):
    methods = [
        "HAC_12",
        "HAC_32",
        "PREWHITE_0",
        "PREWHITE_4",
    ]

    rejects = {name: 0 for name in methods}
    estimated_variances = {
        name: [] for name in methods
    }
    phis = []
    invalid = {name: 0 for name in methods}

    rng = np.random.default_rng(
        20261020 + n * 100 + int(rho * 100)
    )

    for _ in range(repetitions):
        x = simulate_ar1(rng, rho, n)

        pw0, phi = prewhitened_hac_se(
            x, residual_lag=0
        )
        pw4, _ = prewhitened_hac_se(
            x, residual_lag=4
        )

        phis.append(phi)

        ses = {
            "HAC_12": ordinary_hac_se(x, 12),
            "HAC_32": ordinary_hac_se(x, 32),
            "PREWHITE_0": pw0,
            "PREWHITE_4": pw4,
        }

        for name, se in ses.items():
            if not np.isfinite(se) or se <= 0:
                invalid[name] += 1
                continue

            estimated_variances[name].append(se**2)

            # 为公平比较，统一使用渐近正态参考分布
            statistic = abs(x.mean() / se)
            p = 2 * norm.sf(statistic)

            rejects[name] += int(p < 0.05)

    print(f"\n=== rho={rho}, T={n} ===")
    print("Mean estimated phi:", np.mean(phis))

    for name in methods:
        valid = repetitions - invalid[name]
        if valid == 0:
            print(name, "NO VALID ESTIMATES")
            continue

        rejection_rate = rejects[name] / valid

        print(
            f"{name:12s} "
            f"Type-I={rejection_rate:6.2%} "
            f"Invalid={invalid[name]}"
        )


for rho, n in [
    (0.0, 250),
    (0.5, 250),
    (0.9, 80),
    (0.9, 250),
    (0.9, 1000),
]:
    run_experiment(rho, n)
