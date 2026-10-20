import numpy as np
import pandas as pd

from alpha_lab_service.evaluator import evaluate_factor_output


def test_evaluator_emits_descriptive_ic_stability_and_incremental_only():
    times = pd.date_range("2022-01-01", periods=12, tz="UTC")
    index = pd.MultiIndex.from_product([times, [f"A{i}" for i in range(5)]], names=["open_time", "symbol"])
    factor = pd.Series(np.tile(np.arange(5, dtype=float), len(times)), index=index)
    labels = factor * 0.02 + np.repeat(np.arange(len(times)) % 3, 5)
    baseline = factor.rank().reindex(index)
    result = evaluate_factor_output(factor, labels, baseline=baseline, identity="run-1")
    assert result["status"] in {"EXPLORATORY", "DIAGNOSTIC"}
    assert result["identity"] == "run-1"
    assert result["metrics"]["mean_cs_ic"] > 0.9
    assert result["metrics"]["mean_cs_rank_ic"] > 0.9
    assert "stability" in result["metrics"]
    assert "incremental" in result["metrics"]
    assert "bootstrap" not in result["metrics"]
    assert "p_two_sided" not in str(result)
    assert "SUPPORTED" not in str(result)


def test_evaluator_refuses_nonfinite_values_and_misaligned_labels():
    index = pd.MultiIndex.from_product([[pd.Timestamp("2022-01-01", tz="UTC")], ["A", "B", "C", "D", "E"]], names=["open_time", "symbol"])
    factor = pd.Series(np.arange(5, dtype=float), index=index)
    labels = factor.copy()
    labels.iloc[0] = float("nan")
    result = evaluate_factor_output(factor, labels, baseline=None, identity="run-2")
    assert result["status"] == "DIAGNOSTIC"
    assert result["metrics"]["coverage"]["valid_rows"] == 4
    try:
        evaluate_factor_output(factor, labels.iloc[1:], baseline=None, identity="run-3")
    except ValueError:
        pass
    else:
        raise AssertionError("misaligned label index must be rejected")
