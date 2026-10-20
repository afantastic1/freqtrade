
import numpy as np

from alpha_lab.calibration.cs_inference_calibration import (
    simulate_rank_ic,
)

x = simulate_rank_ic(
    seed=20261020,
    assets=10,
    sections=250,
    rho=0.9,
    common_shock=0.0,
    heteroskedastic=False,
    horizon=1,
    effect=0.0,
)

n = len(x)
centered = x - x.mean()

# 样本自协方差
def gamma(k):
    if k == 0:
        return np.dot(centered, centered) / n
    return np.dot(centered[k:], centered[:-k]) / n


gamma0 = gamma(0)

print("样本长度:", n)
print("平均 RankIC:", x.mean())
print("Gamma(0):", gamma0)

print("\n各阶自相关:")
for k in [1, 2, 4, 8, 12, 16, 24, 32, 48, 64]:
    print(
        f"lag={k:2d} "
        f"ACF={gamma(k) / gamma0:+.4f}"
    )

print("\n长期方差分解:")
print("Lag | 正贡献 | 负贡献 | 长期方差 | SE")

for L in [0, 4, 12, 32, 64]:
    contributions = [
        2 * (1 - k / (L + 1)) * gamma(k)
        for k in range(1, L + 1)
    ]

    positive = sum(v for v in contributions if v > 0)
    negative = sum(v for v in contributions if v < 0)

    long_run_var = gamma0 + positive + negative
    se = np.sqrt(max(long_run_var, 0) / n)

    print(
        f"{L:3d} | "
        f"{positive:+.5f} | "
        f"{negative:+.5f} | "
        f"{long_run_var:.5f} | "
        f"{se:.6f}"
    )
