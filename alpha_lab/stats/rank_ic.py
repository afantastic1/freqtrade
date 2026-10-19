"""RankIC 及其不确定性。

为什么需要单独一层：

1. 原实现只报告 RankIC 的点估计，没有任何区间；
2. 逐月 RankIC 的符号被当作方向证据，但未做任何检验；
3. "不同波动率状态下的 RankIC 差异"从未被赋予不确定性。

本模块与 verdict 层保持同一原则：给出点估计 + 区间 + 有效样本量口径，
并且区间一律用 moving-block bootstrap（块长 >= 标签 horizon）计算，
以吸收重叠标签带来的序列依赖。
"""

import numpy as np
import pandas as pd

from alpha_lab.stats.metrics import _average_ranks
from alpha_lab.stats.multiple_testing import two_sided_binomial_p
from alpha_lab.stats.paired_bootstrap import (
    _moving_block_indices,
    resolve_block_size,
)

MIN_OBS = 3


def _clean(values) -> np.ndarray:
    series = pd.Series(values).replace([np.inf, -np.inf], np.nan)
    return series.to_numpy(dtype=float)


def align_factor_label(factor, label) -> tuple[np.ndarray, np.ndarray]:
    """按相同索引对齐并丢弃任一为 NaN 的行。"""
    frame = pd.DataFrame({
        "factor": _clean(factor),
        "label": _clean(label),
    })
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    return (
        frame["factor"].to_numpy(dtype=float),
        frame["label"].to_numpy(dtype=float),
    )


def _rank_ic_from_arrays(f: np.ndarray, y: np.ndarray) -> float:
    if f.shape[0] < MIN_OBS:
        return float("nan")
    if np.unique(f).shape[0] < 2 or np.unique(y).shape[0] < 2:
        return float("nan")

    rf = _average_ranks(f)
    ry = _average_ranks(y)
    rf = rf - rf.mean()
    ry = ry - ry.mean()

    denom = float(np.sqrt(np.dot(rf, rf) * np.dot(ry, ry)))
    if denom <= 0.0:
        return float("nan")

    return float(np.dot(rf, ry) / denom)


def spearman_rank_ic(factor, label) -> float:
    f, y = align_factor_label(factor, label)
    return _rank_ic_from_arrays(f, y)


def monthly_rank_ic(
    factor, label, timestamps
) -> pd.DataFrame:
    """逐月 RankIC（月份由 open_time 决定）。"""
    frame = pd.DataFrame({
        "factor": _clean(factor),
        "label": _clean(label),
        "timestamp": pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True)),
    })
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame["month"] = frame["timestamp"].dt.strftime("%Y-%m")

    rows = []
    for month, group in frame.groupby("month", sort=True):
        rows.append({
            "month": month,
            "n": int(group.shape[0]),
            "rank_ic": _rank_ic_from_arrays(
                group["factor"].to_numpy(dtype=float),
                group["label"].to_numpy(dtype=float),
            ),
        })

    return pd.DataFrame(rows)


def monthly_direction_evidence(
    factor, label, timestamps
) -> dict:
    """逐月 RankIC 的符号证据（点估计 + 精确二项检验）。"""
    monthly = monthly_rank_ic(factor, label, timestamps)
    valid = monthly["rank_ic"].dropna()

    if valid.empty:
        return {
            "months": int(monthly.shape[0]),
            "valid_months": 0,
            "negative_months": 0,
            "negative_ratio": float("nan"),
            "mean_monthly_rank_ic": float("nan"),
            "median_monthly_rank_ic": float("nan"),
            "sign_test_p": float("nan"),
        }

    negative = int((valid < 0).sum())
    trials = int(valid.shape[0])

    return {
        "months": int(monthly.shape[0]),
        "valid_months": trials,
        "negative_months": negative,
        "negative_ratio": float(negative / trials),
        "mean_monthly_rank_ic": float(valid.mean()),
        "median_monthly_rank_ic": float(valid.median()),
        "sign_test_p": two_sided_binomial_p(negative, trials),
    }


def _summarise(draws: np.ndarray, point: float, *, n_boot, block_size) -> dict:
    finite = draws[np.isfinite(draws)]
    valid = int(finite.shape[0])

    if valid < 2:
        return {
            "point": point,
            "boot_sd": float("nan"),
            "ci_95_lower": float("nan"),
            "ci_95_upper": float("nan"),
            "p_two_sided": float("nan"),
            "valid_replicates": valid,
            "n_boot": n_boot,
            "block_size": block_size,
        }

    lower, upper = np.quantile(finite, [0.025, 0.975])
    left = float((finite <= 0.0).mean())
    right = float((finite >= 0.0).mean())

    return {
        "point": point,
        "boot_sd": float(finite.std(ddof=1)),
        "ci_95_lower": float(lower),
        "ci_95_upper": float(upper),
        "p_two_sided": float(
            min(1.0, max(2.0 * min(left, right), 1.0 / valid))
        ),
        "valid_replicates": valid,
        "n_boot": n_boot,
        "block_size": block_size,
    }


def bootstrap_rank_ic(
    factor,
    label,
    *,
    horizon: int,
    block_size: int | None = None,
    n_boot: int = 500,
    random_state: int = 42,
) -> dict:
    """单个 RankIC 的 moving-block bootstrap 区间。"""
    if n_boot < 2:
        raise ValueError("n_boot must be >= 2")

    f, y = align_factor_label(factor, label)
    n = f.shape[0]
    if n < MIN_OBS:
        raise ValueError("not enough aligned observations")

    size = resolve_block_size(n, horizon, requested=block_size)
    point = _rank_ic_from_arrays(f, y)

    rng = np.random.default_rng(random_state)
    n_blocks = int(np.ceil(n / size))
    draws = np.empty(n_boot, dtype=float)

    for i in range(n_boot):
        idx = _moving_block_indices(rng, n, size, n_blocks)
        draws[i] = _rank_ic_from_arrays(f[idx], y[idx])

    result = _summarise(draws, point, n_boot=n_boot, block_size=size)
    result["n"] = n
    return result


def bootstrap_rank_ic_difference(
    factor,
    label,
    group,
    *,
    horizon: int,
    block_size: int | None = None,
    n_boot: int = 500,
    random_state: int = 42,
) -> dict:
    """两个子样本（group=True / False）的 RankIC 之差的区间。

    关键：bootstrap 从**整条序列**上重抽块，而不是在每个子样本内部重抽。
    这样才保留了"哪些行属于哪个状态"本身随时间的聚集性。
    """
    if n_boot < 2:
        raise ValueError("n_boot must be >= 2")

    frame = pd.DataFrame({
        "factor": _clean(factor),
        "label": _clean(label),
        "group": pd.Series(group).astype("boolean").to_numpy(dtype=object),
    }).replace([np.inf, -np.inf], np.nan)

    frame = frame.dropna()
    f = frame["factor"].to_numpy(dtype=float)
    y = frame["label"].to_numpy(dtype=float)
    g = np.asarray(frame["group"].fillna(False), dtype=bool)

    n = f.shape[0]
    if n < 2 * MIN_OBS or g.sum() < MIN_OBS or (~g).sum() < MIN_OBS:
        raise ValueError("not enough observations in each group")

    size = resolve_block_size(n, horizon, requested=block_size)

    def statistic(index) -> float:
        return _rank_ic_from_arrays(f[index][g[index]], y[index][g[index]]) - (
            _rank_ic_from_arrays(f[index][~g[index]], y[index][~g[index]])
        )

    point = statistic(np.arange(n))

    rng = np.random.default_rng(random_state)
    n_blocks = int(np.ceil(n / size))
    draws = np.empty(n_boot, dtype=float)

    for i in range(n_boot):
        idx = _moving_block_indices(rng, n, size, n_blocks)
        if g[idx].sum() < MIN_OBS or (~g[idx]).sum() < MIN_OBS:
            draws[i] = float("nan")
            continue
        draws[i] = statistic(idx)

    result = _summarise(draws, point, n_boot=n_boot, block_size=size)
    result["n"] = n
    result["n_group_true"] = int(g.sum())
    result["n_group_false"] = int((~g).sum())
    return result
