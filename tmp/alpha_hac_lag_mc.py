
import numpy as np

from alpha_lab.calibration.cs_inference_calibration import (
    _hac,
    simulate_rank_ic,
    wilson_interval,
)

REPETITIONS = 500
LAGS = [0, 4, 12, 32, 64]
ALPHA = 0.05


def experiment(rho):
    rejected = {lag: 0 for lag in LAGS}
    covered = {lag: 0 for lag in LAGS}

    for i in range(REPETITIONS):
        # 每次生成一个独立的零效应样本
        x = simulate_rank_ic(
            seed=20261020 + i,
            assets=10,
            sections=250,
            rho=rho,
            common_shock=0.0,
            heteroskedastic=False,
            horizon=1,
            effect=0.0,
        )

        for lag in LAGS:
            low, high, p = _hac(x, lag)

            rejected[lag] += int(p < ALPHA)
            covered[lag] += int(low <= 0 <= high)

    print(f"\n===== rho={rho} =====")
    print("lag | Type-I | 95% Wilson CI | Coverage")

    for lag in LAGS:
        errors = rejected[lag]
        rate = errors / REPETITIONS

        ci = wilson_interval(
            errors, REPETITIONS
        )

        coverage = covered[lag] / REPETITIONS

        print(
            f"{lag:3d} | "
            f"{rate:6.2%} | "
            f"[{ci[0]:.2%}, {ci[1]:.2%}] | "
            f"{coverage:6.2%}"
        )


for rho in [0.0, 0.5, 0.9]:
    experiment(rho)
