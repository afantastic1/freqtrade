
import numpy as np

from alpha_lab.calibration.cs_inference_calibration import (
    _hac,
    simulate_rank_ic,
    wilson_interval,
)

REPETITIONS = 300
SAMPLE_SIZES = [80, 250, 1000]
RHO = 0.9
ALPHA = 0.05


def experiment(n):
    # 比较固定带宽和随样本量增长的带宽
    methods = {
        "fixed_12": 12,
        "fixed_32": 32,
        "sqrt_rule": int(np.ceil(2 * np.sqrt(n))),
    }

    rejected = {name: 0 for name in methods}

    for i in range(REPETITIONS):
        x = simulate_rank_ic(
            seed=20261020 + 10000 * n + i,
            assets=10,
            sections=n,
            rho=RHO,
            common_shock=0.0,
            heteroskedastic=False,
            horizon=1,
            effect=0.0,
        )

        for name, lag in methods.items():
            _, _, p = _hac(x, lag)
            rejected[name] += int(p < ALPHA)

    print(f"\n===== T={n} =====")
    print("Method       Lag   Type-I    Wilson 95%")

    for name, lag in methods.items():
        errors = rejected[name]
        rate = errors / REPETITIONS
        low, high = wilson_interval(errors, REPETITIONS)

        print(
            f"{name:12s} {lag:3d} "
            f"{rate:7.2%}   "
            f"[{low:.2%}, {high:.2%}]"
        )


for n in SAMPLE_SIZES:
    experiment(n)
