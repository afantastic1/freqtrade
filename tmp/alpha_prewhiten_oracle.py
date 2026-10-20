
import numpy as np
from scipy.stats import norm

RHO = 0.9
REPETITIONS = 1000
SAMPLE_SIZES = [80, 250, 1000]


def simulate(rng, rho, n):
    x = np.empty(n)
    x[0] = rng.normal(scale=1 / np.sqrt(1 - rho**2))

    errors = rng.normal(size=n - 1)

    for i in range(1, n):
        x[i] = rho * x[i - 1] + errors[i - 1]

    return x


def true_mean_variance(rho, n):
    gamma0 = 1 / (1 - rho**2)
    k = np.arange(1, n)

    return gamma0 / n * (
        1 + 2 * np.sum((1 - k / n) * rho**k)
    )


def estimate_phi(x):
    previous = x[:-1]
    current = x[1:]

    a = previous - previous.mean()
    b = current - current.mean()

    phi = np.dot(a, b) / np.dot(a, a)
    intercept = current.mean() - phi * previous.mean()

    residuals = current - intercept - phi * previous

    return float(phi), residuals


def residual_long_run_variance(residuals):
    centered = residuals - residuals.mean()
    return float(np.mean(centered**2))


def run(n):
    rng = np.random.default_rng(20261020 + n)

    truth = true_mean_variance(RHO, n)

    estimated = {
        "oracle_exact": [],
        "oracle_phi": [],
        "estimated_phi": [],
    }

    phi_values = []

    for _ in range(REPETITIONS):
        x = simulate(rng, RHO, n)

        phi, residuals = estimate_phi(x)
        phi_values.append(phi)

        # 完全已知理论模型下的真实均值方差
        oracle_exact = truth

        # 知道真实 rho，但仍需估计创新方差
        true_phi_residuals = x[1:] - RHO * x[:-1]
        innovation_var = residual_long_run_variance(
            true_phi_residuals
        )
        oracle_phi = innovation_var / (
            (1 - RHO)**2 * n
        )

        # 完全估计的 AR(1) 预白化
        estimated_phi = (
            residual_long_run_variance(residuals)
            / ((1 - phi)**2 * n)
        )

        variances = {
            "oracle_exact": oracle_exact,
            "oracle_phi": oracle_phi,
            "estimated_phi": estimated_phi,
        }

        for name, var in variances.items():
            if not np.isfinite(var) or var <= 0:
                raise ValueError(
                    f"invalid variance: {name}"
                )

            se = np.sqrt(var)
            pvalue = 2 * norm.sf(abs(x.mean() / se))

            estimated[name].append({
                "variance": var,
                "reject": pvalue < 0.05,
                "phi": phi,
                "z": abs(x.mean() / se),
            })

    print(f"\n===== T={n}, rho={RHO} =====")
    print(
        f"Mean phi: {np.mean(phi_values):.4f}"
    )
    print(
        f"Median phi: {np.median(phi_values):.4f}"
    )
    print(
        f"Phi std: {np.std(phi_values, ddof=1):.4f}"
    )

    print("Method          VarRatio   Type-I")

    for name, records in estimated.items():
        mean_var = np.mean([
            v["variance"] for v in records
        ])
        rejection = np.mean([
            v["reject"] for v in records
        ])

        print(
            f"{name:16s} "
            f"{mean_var / truth:7.3f} "
            f"{rejection:8.2%}"
        )
    
    records = estimated["estimated_phi"]

    # 每次实验的估计方差 / 理论真实方差
    ratios = np.array([
        r["variance"] / truth
        for r in records
    ])

    phis = np.array([
        r["phi"] for r in records
    ])

    z_values = np.array([
        r["z"] for r in records
    ])

    rejects = np.array([
        r["reject"] for r in records
    ])

    print("\nEstimated Phi 详细诊断")
    print("Mean VarRatio:", ratios.mean())
    print("Median VarRatio:", np.median(ratios))

    for q in [0.05, 0.25, 0.5, 0.75, 0.95]:
        print(
            f"VarRatio Q{int(q * 100):02d}: "
            f"{np.quantile(ratios, q):.3f}"
        )

    print("Mean Phi (all):", phis.mean())
    print("Mean Phi (rejected):", phis[rejects].mean())
    print("Mean Phi (accepted):", phis[~rejects].mean())

    print("Mean Z:", z_values.mean())
    print("Z 95th percentile:", np.quantile(z_values, 0.95))
    print("Z 99th percentile:", np.quantile(z_values, 0.99))

    # 根据方差估计大小分成四组
    boundaries = np.quantile(
        ratios, [0.25, 0.5, 0.75]
    )

    groups = np.searchsorted(
        boundaries, ratios, side="right"
    )

    print("\n方差估计分组:")
    print("Quartile | Mean VarRatio | Rejection")

    for group in range(4):
        mask = groups == group

        print(
            f"Q{group + 1}       "
            f"{ratios[mask].mean():.3f}             "
            f"{rejects[mask].mean():.2%}"
        )


for n in SAMPLE_SIZES:
    run(n)
