
import numpy as np
import pandas as pd
import pytest

from alpha_lab.validation.walk_forward import make_walk_forward


def make_test_data():
    times = pd.date_range(
        "2022-01-01",
        "2024-01-01",
        freq="1h",
        inclusive="left",
        tz="UTC",
    )

    n = len(times)

    return pd.DataFrame({
        "open_time": times,
        "decision_time": times + pd.Timedelta(hours=1),
        "label_available_time": (
            times + pd.Timedelta(hours=5)
        ),
        "target": pd.Series(
            np.arange(n) % 2,
            dtype="Int64",
        ),
        "factor": np.arange(n, dtype=float),
    })


def test_eligibility_excludes_samples():
    data = make_test_data()

    eligible = pd.Series(
        True,
        index=data.index,
    )

    # 主动排除 2023 年 3 月的一根 K 线
    excluded_idx = data.index[
        data["open_time"] == pd.Timestamp(
            "2023-03-15 12:00:00",
            tz="UTC",
        )
    ][0]

    eligible.loc[excluded_idx] = False

    folds = list(
        make_walk_forward(
            data=data,
            feature_columns=["factor"],
            test_start="2023-01-01",
            test_end="2023-06-01",
            sample_eligible=eligible,
        )
    )

    all_test_idx = pd.Index(
        np.concatenate([
            fold.test_idx.to_numpy()
            for fold in folds
        ])
    )

    assert excluded_idx not in all_test_idx


def test_eligibility_rejects_wrong_index():
    data = make_test_data()

    eligible = pd.Series(
        True,
        index=pd.RangeIndex(1, len(data) + 1),
    )

    with pytest.raises(
        ValueError,
        match="Eligibility index mismatch",
    ):
        list(
            make_walk_forward(
                data=data,
                feature_columns=["factor"],
                test_start="2023-01-01",
                test_end="2023-06-01",
                sample_eligible=eligible,
            )
        )


def test_eligibility_respects_sorted_data():
    data = make_test_data()

    # 打乱数据行次序，确认掩码仍按索引对齐
    shuffled = data.sample(
        frac=1,
        random_state=42,
    )

    eligible = pd.Series(
        True,
        index=shuffled.index,
    )

    excluded_idx = shuffled.index[100]
    eligible.loc[excluded_idx] = False

    folds = list(
        make_walk_forward(
            data=shuffled,
            feature_columns=["factor"],
            test_start="2023-01-01",
            test_end="2023-06-01",
            sample_eligible=eligible,
        )
    )

    all_idx = set()

    for fold in folds:
        all_idx.update(fold.train_idx)
        all_idx.update(fold.test_idx)

    assert excluded_idx not in all_idx
