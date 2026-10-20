
import numpy as np

from alpha_lab.calibration.cs_inference_calibration import (
    METHODS,
    infer,
    simulate_rank_ic,
    wilson_interval,
)

REPETITIONS = 300
ALPHA = 0.05
SECTIONS = 250
ASSETS = 10

SELECTED = [
    method for method in METHODS
    if method["name"] in {
        "iid_t",
        "hac_lag_12",
        "mbb_lag_12",
    }
]


def run_experiment(rho):
    rejected = {m["name"]: 0 for m in SELECTED}
    covered = {m["name"]: 0 for m in SELECTED}
    autocorrelations = []

    for i in range(REPETITIONS):
        # 不同重复之间使用不同随机种子
        series = simulate_rank_ic(
            seed=20261020 + i,
            assets=ASSETS,
            sections=SECTIONS,
            rho=rho,
            common_shock=0.0,
            heteroskedastic=False,
            horizon=1,
            effect=0.0,
        )

        # 观察 RankIC 本身的一阶自相关
        acf1 = np.corrcoef(
            series[:-1], series[1:]
        )[0, 1]
        autocorrelations.append(acf1)

        for method in SELECTED:
            low, high, pvalue = infer(
                series,
                method,
                n_boot=199,
                seed=300000 + i,
            )

            if pvalue < ALPHA:
                rejected[method["name"]] += 1

            if low <= 0 <= high:
                covered[method["name"]] += 1

    print(f"\n=== rho = {rho} ===")
    print(
        "Mean RankIC lag-1 ACF:",
        round(float(np.mean(autocorrelations)), 3)
    )

    for method in SELECTED:
        name = method["name"]
        errors = rejected[name]
        rate = errors / REPETITIONS

        ci_low, ci_high = wilson_interval(
            errors, REPETITIONS
        )

        coverage = covered[name] / REPETITIONS

        print(
            f"{name:12s} "
            f"Type-I={rate:.2%} "
            f"Wilson95%=[{ci_low:.2%}, {ci_high:.2%}] "
            f"Coverage={coverage:.2%}"
        )


if __name__ == "__main__":
    for rho in (0.0, 0.5, 0.9):
        run_experiment(rho)
