
import numpy as np


def theoretical_hac_ratio(rho, lag):
    """
    AR(1) 下，Bartlett HAC 的总体长期方差
    与真实长期方差的比例。
    """
    gamma0 = 1.0 / (1.0 - rho**2)

    omega = 1.0 / (1.0 - rho)**2

    omega_hac = gamma0

    for k in range(1, lag + 1):
        gamma_k = gamma0 * rho**k

        weight = 1 - k / (lag + 1)

        omega_hac += 2 * weight * gamma_k

    return omega_hac / omega


print("rho  | lag | theoretical ratio")

for rho in [0.0, 0.5, 0.9]:
    for lag in [0, 4, 12, 32, 64, 128]:
        ratio = theoretical_hac_ratio(rho, lag)

        print(
            f"{rho:.1f}  | "
            f"{lag:3d} | "
            f"{ratio:.4f}"
        )
