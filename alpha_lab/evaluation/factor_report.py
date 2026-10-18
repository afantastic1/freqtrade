
import numpy as np
import pandas as pd

from alpha_lab.evaluation.ic import evaluate_ic


def _group_ic(group: pd.DataFrame) -> dict:
    """计算一个时间分组内的 IC。"""
    if (
        len(group) < 3
        or group["factor"].nunique() < 2
        or group["label"].nunique() < 2
    ):
        return {
            "n": len(group),
            "ic": np.nan,
            "rank_ic": np.nan,
        }

    result = evaluate_ic(
        group["factor"],
        group["label"],
    )

    return {
        "n": result["n_obs"],
        "ic": result["ic"],
        "rank_ic": result["rank_ic"],
    }


def evaluate_factor(
    factor: pd.Series,
    label: pd.Series,
    timestamps: pd.Series,
    *,
    n_quantiles: int = 10,
) -> dict:
    """
    单因子描述性评价。

    注意：
    1. 所有输入必须按相同索引对齐。
    2. 分位数使用完整年份事后计算，仅用于研究描述。
    3. 返回结果不代表独立 OOS 或可交易收益。
    """
    if not (
        factor.index.equals(label.index)
        and factor.index.equals(timestamps.index)
    ):
        raise ValueError("Input indexes must match")

    if not factor.index.is_unique:
        raise ValueError("Input index must be unique")

    if not isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        raise ValueError("timestamps must be timezone-aware")

    if timestamps.isna().any():
        raise ValueError("timestamps contain NaT")

    if n_quantiles < 2:
        raise ValueError("n_quantiles must be >= 2")

    raw = pd.DataFrame({
        "timestamp": timestamps.dt.tz_convert("UTC"),
        "factor": pd.to_numeric(factor, errors="raise"),
        "label": pd.to_numeric(label, errors="raise"),
    })

    if raw["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps")

    if not raw["timestamp"].is_monotonic_increasing:
        raise ValueError("Timestamps must be sorted")

    total = len(raw)

    if total == 0:
        raise ValueError("Empty input")

    raw = raw.replace([np.inf, -np.inf], np.nan)

    valid = raw.dropna(subset=["factor", "label"]).copy()

    if len(valid) < 3:
        raise ValueError("Not enough valid observations")

    overall = _group_ic(valid)

    valid["year"] = valid["timestamp"].dt.year
    valid["month"] = valid["timestamp"].dt.strftime("%Y-%m")

    monthly_rows = []
    for month, group in valid.groupby("month", sort=True):
        monthly_rows.append({
            "month": month,
            **_group_ic(group),
        })

    monthly = pd.DataFrame(monthly_rows)

    yearly_rows = []
    for year, group in valid.groupby("year", sort=True):
        yearly_rows.append({
            "year": int(year),
            **_group_ic(group),
        })

    yearly = pd.DataFrame(yearly_rows)

    # 与此前的 Momentum 实验保持同一口径：
    # 按完整年份的因子排序，事后划分分位数。
    valid["quantile"] = pd.Series(
        pd.NA,
        index=valid.index,
        dtype="Int64",
    )

    for year, group in valid.groupby("year"):
        if len(group) < n_quantiles:
            continue

        # 使用 rank(method="first") 避免 qcut 因重复值失败。
        # 此处用事后年度排名，仅可用于描述性分析。
        ranks = group["factor"].rank(method="first")

        valid.loc[group.index, "quantile"] = pd.qcut(
            ranks,
            q=n_quantiles,
            labels=False,
        ).to_numpy()

    grouped = valid.dropna(subset=["quantile"]).copy()
    grouped["quantile"] = grouped["quantile"].astype(int)

    deciles = (
        grouped.groupby(["year", "quantile"])["label"]
        .agg(
            n="count",
            mean="mean",
            median="median",
            positive_rate=lambda x: (x > 0).mean(),
            p05=lambda x: x.quantile(0.05),
            p95=lambda x: x.quantile(0.95),
        )
        .reset_index()
    )

    # 收益统一转换为 bps
    for col in ("mean", "median", "p05", "p95"):
        deciles[col] *= 10000

    valid_monthly = monthly["rank_ic"].dropna()

    stability = {
        "months": len(monthly),
        "valid_months": len(valid_monthly),
        "negative_rank_ic_ratio": (
            float((valid_monthly < 0).mean())
            if len(valid_monthly) else np.nan
        ),
        "mean_monthly_rank_ic": (
            float(valid_monthly.mean())
            if len(valid_monthly) else np.nan
        ),
        "median_monthly_rank_ic": (
            float(valid_monthly.median())
            if len(valid_monthly) else np.nan
        ),
    }

    return {
        "quality": {
            "total": total,
            "valid": len(valid),
            "missing": total - len(valid),
            "missing_ratio": 1 - len(valid) / total,
        },
        "overall": overall,
        "monthly": monthly,
        "yearly": yearly,
        "deciles": deciles,
        "stability": stability,
    }
