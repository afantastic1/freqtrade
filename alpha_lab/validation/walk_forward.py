
from dataclasses import dataclass

import numpy as np
import pandas as pd


TIME_COLUMNS = (
    "open_time",
    "decision_time",
    "label_available_time",
)

RESERVED_COLUMNS = {
    "open_time",
    "decision_time",
    "entry_time",
    "label_end_time",
    "label_available_time",
    "reference_return",
    "entry_time_feasible",
    "target",
}


@dataclass(frozen=True)
class WalkForwardFold:
    month: str
    train_idx: pd.Index
    test_idx: pd.Index
    train_time: pd.Timestamp


def make_walk_forward(
    data: pd.DataFrame,
    feature_columns: list[str],
    test_start: str = "2023-01-01",
    test_end: str = "2025-01-01",
    train_months: int = 12,
    retrain_months: int = 1,
    min_train_samples: int = 1000,
    sample_eligible: pd.Series | None = None,
):
    """
    通用多因子滚动训练切分器。

    训练时仅使用标签已经可用的历史样本。
    一个模型固定预测 retrain_months 个月。
    """

    if train_months <= 0 or retrain_months <= 0:
        raise ValueError("Training months must be positive")

    if min_train_samples <= 0:
        raise ValueError("min_train_samples must be positive")

    if not feature_columns:
        raise ValueError("At least one feature is required")

    if len(feature_columns) != len(set(feature_columns)):
        raise ValueError("Duplicate feature names")

    if RESERVED_COLUMNS.intersection(feature_columns):
        raise ValueError("Reserved column used as feature")

    required = (
        set(TIME_COLUMNS)
        | {"target"}
        | set(feature_columns)
    )

    missing = required - set(data.columns)

    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    if not data.columns.is_unique:
        raise ValueError("Duplicate dataset columns")

    if not data.index.is_unique:
        raise ValueError("Dataset index must be unique")

    df = data.sort_values("open_time").copy()

    
    for col in TIME_COLUMNS:
        if not isinstance(df[col].dtype, pd.DatetimeTZDtype):
            raise ValueError(f"{col} must be timezone-aware")

        # open_time 和 decision_time 必须始终有效。
        # label_available_time 可以为 NaT，
        # 因为最后几根 K 线可能没有完整的未来标签。
        if col != "label_available_time" and df[col].isna().any():
            raise ValueError(f"{col} contains NaT")

        df[col] = df[col].dt.tz_convert("UTC")


    if df["open_time"].duplicated().any():
        raise ValueError("Duplicate open_time")

    if (
        df["decision_time"] < df["open_time"]
    ).any():
        raise ValueError("Invalid decision time")

    
    # 只有存在标签可用时间的行，才检查时间顺序
    valid_label_time = df["label_available_time"].notna()

    if (
        df.loc[valid_label_time, "label_available_time"]
        <= df.loc[valid_label_time, "decision_time"]
    ).any():
        raise ValueError("Invalid label timing")

    # 有目标标签却没有可用时间，属于严重的数据错误
    if (
        df["target"].notna()
        & ~valid_label_time
    ).any():
        raise ValueError("Target has no availability time")


    if not df["decision_time"].is_monotonic_increasing:
        raise ValueError("Decision times are not monotonic")

    # 保证因子列为数值，不能隐式接收字符串对象
    try:
        values = df[feature_columns].to_numpy(dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError("Non-numeric feature") from exc

    feature_valid = pd.Series(
        np.isfinite(values).all(axis=1),
        index=df.index,
    )

        # 限定三组模型共同可用的训练与预测样本
    if sample_eligible is not None:
        if not isinstance(sample_eligible, pd.Series):
            raise TypeError("sample_eligible must be a pandas Series")

        if not sample_eligible.index.equals(data.index):
            raise ValueError("Eligibility index mismatch")

        if sample_eligible.isna().any():
            raise ValueError("Eligibility contains NA")

        if not pd.api.types.is_bool_dtype(sample_eligible.dtype):
            raise ValueError("Eligibility must be boolean")

        # 因为上面按 open_time 排过序，
        # 必须根据原始索引重新排列掩码
        eligible = sample_eligible.reindex(df.index)

        feature_valid = feature_valid & eligible
    
    def utc_timestamp(value):
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")

    start = utc_timestamp(test_start)
    end = utc_timestamp(test_end)

    if start >= end:
        raise ValueError("Invalid test period")

    month_starts = pd.date_range(
        start=start,
        end=end,
        freq=pd.DateOffset(months=retrain_months),
        inclusive="left",
    )

    for period_start in month_starts:
        period_end = min(
            period_start
            + pd.DateOffset(months=retrain_months),
            end,
        )

        # 固定训练时刻：
        # 使用本期第一根原始 K 线的决策时间，
        # 不受特征缺失情况影响。
        period_rows = df.loc[
            (df["open_time"] >= period_start)
            & (df["open_time"] < period_end)
        ]

        if period_rows.empty:
            raise ValueError(
                f"No rows in {period_start:%Y-%m}"
            )

        train_time = period_rows["decision_time"].iloc[0]

        train_start = train_time - pd.DateOffset(
            months=train_months
        )

        train_mask = (
            (df["open_time"] >= train_start)
            & (df["open_time"] < period_start)
            & (df["label_available_time"] <= train_time)
            & feature_valid
            & df["target"].notna()
        )

        test_mask = (
            (df["open_time"] >= period_start)
            & (df["open_time"] < period_end)
            & (df["decision_time"] >= train_time)
            & feature_valid
            & df["target"].notna()
        )

        train = df.loc[train_mask]
        test = df.loc[test_mask]

        if len(train) < min_train_samples:
            raise ValueError(
                f"{period_start:%Y-%m}: only "
                f"{len(train)} training samples"
            )

        if test.empty:
            raise ValueError(
                f"No valid test samples: {period_start:%Y-%m}"
            )

        yield WalkForwardFold(
            month=period_start.strftime("%Y-%m"),
            train_idx=train.index.copy(),
            test_idx=test.index.copy(),
            train_time=train_time,
        )
