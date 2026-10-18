
import numpy as np
import pandas as pd
import pytest


import numpy as np
import pandas as pd
from alpha_lab.validation.walk_forward import make_walk_forward

def make_dataset():
    """
    构造用于 Walk-Forward 测试的模拟数据。

    2022-11 到 2023-04，每小时一条记录。
    不依赖真实 BTC 数据。
    """
    times = pd.date_range(
        start="2022-11-01",
        end="2023-04-01",
        freq="1h",
        inclusive="left",
        tz="UTC",
    )

    n = len(times)

    rng = np.random.default_rng(42)

    factor = rng.normal(size=n)
    target = (rng.random(n) > 0.5).astype(int)

    return pd.DataFrame({
        "open_time": times,
        "decision_time": (
            times + pd.Timedelta(hours=1, seconds=1)
        ),
        "label_available_time": (
            times + pd.Timedelta(hours=5, seconds=1)
        ),
        "factor": factor,
        "target": pd.Series(target, dtype="Int64"),
    })



def make_multifactor_dataset():
    df = make_dataset()

    df["momentum_4"] = df["factor"].astype(float)
    df["momentum_12"] = (
        df["factor"].astype(float) * 0.5
    )

    return df


def get_multifactor_fold(df):
    return next(make_walk_forward(
        data=df,
        feature_columns=["momentum_4", "momentum_12"],
        test_start="2023-01-01",
        test_end="2023-02-01",
        train_months=2,
        min_train_samples=100,
    ))


def test_multifactor_walk_forward():
    df = make_multifactor_dataset()
    fold = get_multifactor_fold(df)

    train = df.loc[fold.train_idx]
    test = df.loc[fold.test_idx]

    assert len(train) > 100
    assert len(test) > 0

    assert set(fold.train_idx).isdisjoint(
        set(fold.test_idx)
    )


def test_multifactor_missing_value():
    df = make_multifactor_dataset()

    target_time = pd.Timestamp(
        "2023-01-02T00:00:00Z"
    )
    row = df["open_time"] == target_time

    df.loc[row, "momentum_12"] = np.nan

    fold = get_multifactor_fold(df)
    test = df.loc[fold.test_idx]

    assert not (test["open_time"] == target_time).any()


def test_multifactor_future_label_purging():
    df = make_multifactor_dataset()

    target_time = pd.Timestamp(
        "2022-12-31T23:00:00Z"
    )
    row = df["open_time"] == target_time

    df.loc[row, "label_available_time"] = (
        pd.Timestamp("2023-01-02T00:00:00Z")
    )

    fold = get_multifactor_fold(df)
    train = df.loc[fold.train_idx]

    assert not (train["open_time"] == target_time).any()

    assert (
        train["label_available_time"] <= fold.train_time
    ).all()


def test_invalid_feature_column():
    df = make_multifactor_dataset()

    with pytest.raises(ValueError, match="Missing columns"):
        list(make_walk_forward(
            df,
            feature_columns=["unknown_factor"],
            test_start="2023-01-01",
            test_end="2023-02-01",
            train_months=2,
            min_train_samples=100,
        ))


def test_reserved_feature_column():
    df = make_multifactor_dataset()

    with pytest.raises(ValueError, match="Reserved column"):
        list(make_walk_forward(
            df,
            feature_columns=["target"],
            test_start="2023-01-01",
            test_end="2023-02-01",
            train_months=2,
            min_train_samples=100,
        ))


def test_missing_label_time_with_target_rejected():
    df = make_multifactor_dataset()

    df.loc[10, "label_available_time"] = pd.NaT

    with pytest.raises(
        ValueError, match="Target has no availability time"
    ):
        list(make_walk_forward(
            df,
            feature_columns=["momentum_4"],
            test_start="2023-01-01",
            test_end="2023-02-01",
            train_months=2,
            min_train_samples=100,
        ))

def test_unfinished_labels_are_allowed():
    """数据尾部尚未完成的标签不应导致整个实验失败。"""
    df = make_multifactor_dataset()

    # 最后四条样本尚未完成未来 4h 标签
    tail_idx = df.index[-4:]

    df.loc[tail_idx, "target"] = pd.NA
    df.loc[tail_idx, "label_available_time"] = pd.NaT

    folds = list(make_walk_forward(
        data=df,
        feature_columns=["momentum_4", "momentum_12"],
        test_start="2023-03-01",
        test_end="2023-04-01",
        train_months=2,
        min_train_samples=100,
    ))

    assert len(folds) == 1

    test = df.loc[folds[0].test_idx]

    assert not test.index.isin(tail_idx).any()
    assert test["target"].notna().all()
