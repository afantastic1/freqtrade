"""配对 block bootstrap。

与 alpha_lab.evaluation.incremental_stability 的关键区别：

1. **配对**：基线与组合模型的损失在**同一批行**上做差，
   每个 bootstrap 复制都使用同一组行索引，因此比较的是配对的增量。
2. **块长由标签 horizon 决定**：块长必须 >= horizon，
   否则块内无法覆盖一个完整标签窗口，会低估序列依赖。
   ``resolve_block_size`` 强制执行这一规则。
3. **覆盖 AUC**：原实现对 AUC 没有任何不确定性度量，
   只报告一个点差。这里对每次复制重算 AUC，得到 ΔAUC 的置信区间。
4. **同时给出区间与 p 值**，但不允许只用 p 值下结论（见 verdict 模块）。

注意：AUC 是**整体池化**口径（与 runner 一致），不是逐折平均。
"""

from dataclasses import dataclass

import numpy as np

from alpha_lab.stats.metrics import (
    brier_loss_per_sample,
    fast_auc,
    logloss_per_sample,
)

SUPPORTED_METRICS = ("auc", "logloss", "brier")


@dataclass(frozen=True)
class BootstrapResult:
    metric: str
    point: float
    boot_mean: float
    boot_sd: float
    ci_95_lower: float
    ci_95_upper: float
    p_two_sided: float
    positive_ratio: float
    n_boot: int
    block_size: int
    valid_replicates: int

    @property
    def ci_excludes_zero(self) -> bool:
        return self.ci_95_lower > 0.0 or self.ci_95_upper < 0.0

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "point": self.point,
            "boot_mean": self.boot_mean,
            "boot_sd": self.boot_sd,
            "ci_95_lower": self.ci_95_lower,
            "ci_95_upper": self.ci_95_upper,
            "p_two_sided": self.p_two_sided,
            "positive_ratio": self.positive_ratio,
            "n_boot": self.n_boot,
            "block_size": self.block_size,
            "valid_replicates": self.valid_replicates,
            "ci_excludes_zero": self.ci_excludes_zero,
        }


def resolve_block_size(n: int, horizon: int, requested: int | None = None) -> int:
    """块长必须 >= horizon（保证块内覆盖完整标签窗口），且 <= n。"""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if n < 2:
        raise ValueError("need at least 2 rows")

    size = int(requested) if requested is not None else int(horizon)

    if requested is not None and requested < horizon:
        raise ValueError(
            f"block_size ({requested}) must be >= horizon ({horizon}) "
            "to cover a full label window"
        )

    if size < 1:
        raise ValueError("block_size must be positive")

    return int(min(size, n))


def _moving_block_indices(
    rng: np.random.Generator,
    n: int,
    block_size: int,
    n_blocks: int,
) -> np.ndarray:
    """非环绕 moving block bootstrap 的行索引。

    完全向量化：`block=1` 时 `n_blocks == n`，逐块 `np.arange` 会产生
    O(n) 次 Python 调用，是 bootstrap 的主要瓶颈。这里改成
    `starts[:, None] + arange(block_size)` 的一次性广播，且使用与
    逐块版本**完全相同的** `rng.integers` 调用，因此结果逐位一致。
    """
    max_start = n - block_size
    starts = rng.integers(0, max_start + 1, size=n_blocks)
    offsets = np.arange(block_size, dtype=starts.dtype)
    indices = (starts[:, None] + offsets[None, :]).ravel()
    return indices[:n]


def paired_block_bootstrap(
    y,
    baseline_prob,
    combined_prob,
    *,
    block_size: int,
    n_boot: int = 1000,
    random_state: int = 42,
    metrics: tuple[str, ...] = SUPPORTED_METRICS,
) -> dict[str, BootstrapResult]:
    """在同一批行上对三个指标做配对 moving-block bootstrap。

    返回 dict: metric -> BootstrapResult，符号约定为
    "正值代表组合模型更好"。
    """
    y = np.asarray(y, dtype=float).ravel()
    pb = np.asarray(baseline_prob, dtype=float).ravel()
    pc = np.asarray(combined_prob, dtype=float).ravel()

    if not (y.shape == pb.shape == pc.shape):
        raise ValueError("y, baseline_prob, combined_prob must align")
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    if not np.isfinite(y).all() or not np.isfinite(pb).all() or not np.isfinite(pc).all():
        raise ValueError("non-finite inputs")
    if not np.isin(y, [0.0, 1.0]).all():
        raise ValueError("labels must be binary")

    unknown = set(metrics) - set(SUPPORTED_METRICS)
    if unknown:
        raise ValueError(f"unsupported metrics: {sorted(unknown)}")

    n = y.shape[0]
    n_blocks = int(np.ceil(n / block_size))

    base_logloss = logloss_per_sample(y, pb)
    comb_logloss = logloss_per_sample(y, pc)
    base_brier = brier_loss_per_sample(y, pb)
    comb_brier = brier_loss_per_sample(y, pc)

    def point_estimates(idx) -> dict[str, float]:
        return {
            "auc": fast_auc(y[idx], pc[idx]) - fast_auc(y[idx], pb[idx]),
            "logloss": float(
                (base_logloss[idx] - comb_logloss[idx]).mean()
            ),
            "brier": float((base_brier[idx] - comb_brier[idx]).mean()),
        }

    full_idx = np.arange(n)
    points = point_estimates(full_idx)

    draws = {name: np.empty(n_boot, dtype=float) for name in metrics}
    rng = np.random.default_rng(random_state)

    for i in range(n_boot):
        idx = _moving_block_indices(rng, n, block_size, n_blocks)
        replicate = point_estimates(idx)
        for name in metrics:
            draws[name][i] = replicate[name]

    results: dict[str, BootstrapResult] = {}

    for name in metrics:
        values = draws[name]
        finite = values[np.isfinite(values)]
        valid = int(finite.shape[0])

        if valid < 2:
            results[name] = BootstrapResult(
                metric=name,
                point=float(points[name]),
                boot_mean=float("nan"),
                boot_sd=float("nan"),
                ci_95_lower=float("nan"),
                ci_95_upper=float("nan"),
                p_two_sided=float("nan"),
                positive_ratio=float("nan"),
                n_boot=n_boot,
                block_size=block_size,
                valid_replicates=valid,
            )
            continue

        lower, upper = np.quantile(finite, [0.025, 0.975])
        positive_ratio = float((finite > 0.0).mean())

        # 用中心化 bootstrap 构造 H0: effect=0 的零分布。未中心化抽样
        # 分布围绕观测效应，直接计算其跨零比例并不是校准的检验。
        null_draws = finite - float(points[name])
        extreme = int((np.abs(null_draws) >= abs(float(points[name]))).sum())
        p_two_sided = float((extreme + 1) / (valid + 1))

        results[name] = BootstrapResult(
            metric=name,
            point=float(points[name]),
            boot_mean=float(finite.mean()),
            boot_sd=float(finite.std(ddof=1)),
            ci_95_lower=float(lower),
            ci_95_upper=float(upper),
            p_two_sided=p_two_sided,
            positive_ratio=positive_ratio,
            n_boot=n_boot,
            block_size=block_size,
            valid_replicates=valid,
        )

    return results
