
import numpy as np
import pandas as pd
import pytest

from alpha_lab.evaluation.factor_report import evaluate_factor


def make_inputs():
    timestamps = pd.Series(
        pd.date_range(
            "2024-01-01",
            periods=100,
            freq="1h",
            tz="UTC",
        )
    )

    factor = pd.Series(
        np.arange(100, dtype=float)
    )

    # 与因子严格负相关
    label = pd.Series(
        -np.arange(100, dtype=float)
    )

    return factor, label, timestamps


def test_perfect_negative_ic():
    factor, label, timestamps = make_inputs()

    report = evaluate_factor(
        factor, label, timestamps
    )

    assert report["overall"]["ic"] == pytest.approx(-1.0)
    assert report["overall"]["rank_ic"] == pytest.approx(-1.0)
    assert report["quality"]["valid"] == 100


def test_missing_observations():
    factor, label, timestamps = make_inputs()

    factor.iloc[5] = np.nan
    label.iloc[10] = np.inf

    report = evaluate_factor(
        factor, label, timestamps
    )

    assert report["quality"]["total"] == 100
    assert report["quality"]["valid"] == 98
    assert report["quality"]["missing"] == 2


def test_decile_distribution():
    factor, label, timestamps = make_inputs()

    report = evaluate_factor(
        factor, label, timestamps
    )

    deciles = report["deciles"]

    assert len(deciles) == 10
    assert deciles["n"].sum() == 100

    # 因子越高，未来收益越低
    assert deciles.iloc[0]["mean"] > deciles.iloc[-1]["mean"]


def test_index_mismatch():
    factor, label, timestamps = make_inputs()

    label.index = label.index + 1

    with pytest.raises(
        ValueError, match="Input indexes must match"
    ):
        evaluate_factor(factor, label, timestamps)


def test_naive_timestamp_rejected():
    factor, label, timestamps = make_inputs()

    timestamps = timestamps.dt.tz_localize(None)

    with pytest.raises(
        ValueError, match="timezone-aware"
    ):
        evaluate_factor(factor, label, timestamps)


def test_no_input_mutation():
    factor, label, timestamps = make_inputs()

    original_factor = factor.copy(deep=True)
    original_label = label.copy(deep=True)

    evaluate_factor(factor, label, timestamps)

    pd.testing.assert_series_equal(
        factor, original_factor
    )
    pd.testing.assert_series_equal(
        label, original_label
    )
def test_strict_numpy_error_policy():
    factor, label, timestamps = make_inputs()

    with np.errstate(all="raise"):
        before = np.geterr().copy()

        report = evaluate_factor(
            factor, label, timestamps
        )

        assert np.geterr() == before

    deciles = report["deciles"]

    assert len(deciles) == 10
    assert deciles["n"].sum() == 100