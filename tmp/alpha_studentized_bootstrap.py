
import numpy as np
from scipy.stats import norm

REPETITIONS = 250
BOOTSTRAPS = 199
ALPHA = 0.05


def generate_ar1(rng, phi, sigma, n, count):
    """零均值、平稳 AR(1) 模拟。"""
    x = np.empty((count, n))

    x[:, 0] = rng.normal(
        scale=sigma / np.sqrt(1 - phi**2),
        size=count,
    )

    shocks = rng.normal(
        scale=sigma, size=(count, n - 1)
    )

    for t in range(1, n):
        x[:, t] = phi * x[:, t-1] + shocks[:, t-1]

    return x


def fit_studentized(x):
    """每条序列独立拟合 AR(1)，计算学生化统计量。"""
    x = np.atleast_2d(np.asarray(x, dtype=float))
    n = x.shape[1]

    previous = x[:, :-1]
    current = x[:, 1:]

    a = previous - previous.mean(axis=1, keepdims=True)
    b = current - current.mean(axis=1, keepdims=True)

    phi = (a * b).sum(axis=1) / (a * a).sum(axis=1)

    intercept = (
        current.mean(axis=1)
        - phi * previous.mean(axis=1)
    )

    residuals = (
        current - intercept[:, None]
        - phi[:, None] * previous
    )

    sigma = np.sqrt((residuals**2).mean(axis=1))

    se = sigma / (
        np.abs(1 - phi) * np.sqrt(n)
    )

    z = np.abs(x.mean(axis=1) / se)

    return z, phi, sigma


def experiment(rho, n):
    rng_data = np.random.default_rng(
        20261020 + n + int(rho * 100)
    )
    rng_boot = np.random.default_rng(
        30261020 + n + int(rho * 100)
    )

    normal_reject = 0
    bootstrap_reject = 0
    invalid = 0
    critical_values = []

    samples = generate_ar1(
        rng_data, rho, 1.0, n, REPETITIONS
    )

    for x in samples:
        z, phi, sigma = fit_studentized(x)
        z, phi, sigma = z[0], phi[0], sigma[0]

        # 不允许用非平稳拟合结果生成 Bootstrap
        if not np.isfinite(z) or abs(phi) >= 1:
            invalid += 1
            continue

        # 普通渐近正态检验
        p_normal = 2 * norm.sf(z)

        # 在 H0: mean = 0 下生成 Bootstrap 样本
        simulated = generate_ar1(
            rng_boot, phi, sigma, n, BOOTSTRAPS
        )

        # 每次 Bootstrap 都重新拟合 AR(1)
        z_boot, _, _ = fit_studentized(simulated)

        if not np.isfinite(z_boot).all():
            invalid += 1
            continue

        p_boot = (
            1 + np.count_nonzero(z_boot >= z)
        ) / (BOOTSTRAPS + 1)

        normal_reject += int(p_normal < ALPHA)
        bootstrap_reject += int(p_boot < ALPHA)

        critical_values.append(
            np.quantile(z_boot, 0.95)
        )

    print(f"\n=== rho={rho}, T={n} ===")
    print("Normal rejection:", normal_reject / REPETITIONS)
    print("Bootstrap rejection:", bootstrap_reject / REPETITIONS)
    print("Invalid:", invalid)
    print(
        "Mean Bootstrap critical:",
        np.mean(critical_values)
        if critical_values else None
    )


for rho in (0.0, 0.5, 0.9):
    for n in (80, 250):
        experiment(rho, n)
