
import numpy as np
import pandas as pd
import pytest

from alpha_lab.evaluation.incremental_stability import (
    evaluate_stability,
)


def make_inputs(n=240):
    times = pd.Series(
        pd.date_range(
            "2023-01-01",
            periods=n,
            freq="h",
            tz="UTC",
        )
    )

    y = pd.Series(np.arange(n) % 2)

    # 基线概率始终为 0.5
    base = pd.Series(np.full(n, 0.5))

    # 组合模型始终朝正确标签方向预测
    combined = pd.Series(
        np.where(y.to_numpy() == 1, 0.7, 0.3)
    )

    return times, y, base, combined


def test_improved_model_has_positive_gain():
    times, y, base, combined = make_inputs()

    result = evaluate_stability(
        times,
        y,
        base,
        combined,
        bootstrap_samples=100,
        block_size=24,
    )

    assert result["summary"]["logloss"]["mean"] > 0
    assert result["summary"]["brier"]["mean"] > 0
    assert (
        result["summary"]["logloss"]["ci_95_lower"] > 0
    )
    assert result["summary"]["positive_logloss_months"] == 1


def test_identical_models_have_zero_gain():
    times, y, base, _ = make_inputs()

    result = evaluate_stability(
        times,
        y,
        base,
        base.copy(),
        bootstrap_samples=100,
        block_size=24,
    )

    assert result["summary"]["logloss"]["mean"] == pytest.approx(0)
    assert result["summary"]["brier"]["mean"] == pytest.approx(0)


def test_invalid_probabilities_rejected():
    times, y, base, combined = make_inputs()

    combined.iloc[0] = 1.5

    with pytest.raises(
        ValueError,
        match="Probabilities",
    ):
        evaluate_stability(
            times,
            y,
            base,
            combined,
        )


def test_bootstrap_reproducible():
    inputs = make_inputs()

    a = evaluate_stability(
        *inputs,
        bootstrap_samples=100,
        block_size=24,
        random_state=42,
    )

    b = evaluate_stability(
        *inputs,
        bootstrap_samples=100,
        block_size=24,
        random_state=42,
    )

    assert a == b
