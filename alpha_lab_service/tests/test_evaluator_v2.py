import pandas as pd
import pytest
from types import SimpleNamespace

from alpha_lab_service.evaluator import evaluate_authorized_factor_output
from alpha_lab_service.evaluation_v2 import evaluate_authorized_factor_output as evaluate_v2
from alpha_lab_service.tests.test_v2_chunks import make_run


def factor_series(run):
    index = pd.MultiIndex.from_tuples(
        [(stamp, symbol) for stamp in run.evaluation_timestamps for symbol in run.research_request.symbols],
        names=["open_time", "symbol"],
    )
    return pd.Series([1.0] * len(index), index=index, dtype=float)


def test_v2_evaluator_accepts_complete_authorized_projection():
    run = make_run(count=2)
    report = evaluate_authorized_factor_output(factor_series(run), run, identity="run-1")
    assert report["status"] == "NOT_EVALUABLE"
    assert report["reason"] == "INSUFFICIENT_VALID_OBSERVATIONS"
    assert report["identity"] == "run-1"


def test_v2_evaluator_rejects_partial_or_reordered_projection():
    run = make_run(count=2)
    factor = factor_series(run)
    with pytest.raises(ValueError, match="complete authorized"):
        evaluate_authorized_factor_output(factor.iloc[:1], run, identity="run-1")
    with pytest.raises(ValueError, match="complete authorized"):
        evaluate_authorized_factor_output(factor.iloc[::-1], run, identity="run-1")


def _evaluation_run(mode, timestamps, symbols):
    frame = pd.DataFrame([
        {"decision_time": stamp, "symbol": symbol,
         "label_value": float(i + 1) / 1000}
        for i, (stamp, symbol) in enumerate(
            (pair for stamp in timestamps for pair in [(stamp, symbol) for symbol in symbols])
        )
    ])
    return SimpleNamespace(
        research_request=SimpleNamespace(mode=mode, symbols=tuple(symbols)),
        evaluation_timestamps=tuple(timestamps),
        dataset=SimpleNamespace(evaluator_labels=SimpleNamespace(frame=frame)),
    )


def test_time_series_evaluator_reports_ic_rank_ic_monthly_yearly_and_coverage():
    timestamps = pd.date_range("2025-01-01", periods=8, freq="MS", tz="UTC")
    run = _evaluation_run("time_series", timestamps, ["BTCUSDT"])
    index = pd.MultiIndex.from_tuples([(stamp, "BTCUSDT") for stamp in timestamps], names=["open_time", "symbol"])
    result = evaluate_v2(pd.Series(range(8), index=index, dtype=float), run, identity="ts")
    metrics = result["metrics"]
    assert result["mode"] == "time_series"
    assert metrics["rank_ic"] == pytest.approx(1.0)
    assert metrics["coverage"]["valid_rows"] == 8
    assert len(metrics["monthly"]) == 8
    assert len(metrics["yearly"]) == 1
    assert set(metrics["monthly"]["2025-01"]) == {"ic", "rank_ic"}
    assert "per_time" not in metrics
    assert result["formal_inference_status"] == "BLOCKED"


def test_cross_sectional_evaluator_reports_quantiles_asset_counts_and_stability():
    timestamps = pd.date_range("2025-01-01", periods=8, freq="D", tz="UTC")
    symbols = [f"A{i}" for i in range(5)]
    run = _evaluation_run("cross_sectional", timestamps, symbols)
    index = pd.MultiIndex.from_product([timestamps, symbols], names=["open_time", "symbol"])
    factor = pd.Series(list(range(5)) * len(timestamps), index=index, dtype=float)
    result = evaluate_v2(factor, run, identity="cs")
    metrics = result["metrics"]
    assert result["mode"] == "cross_sectional"
    assert metrics["mean_cs_rank_ic"] == pytest.approx(1.0)
    assert metrics["asset_count"]["min"] == 5
    assert metrics["quantile_returns"]
    assert result["formal_inference_status"] == "BLOCKED"


@pytest.mark.parametrize("mode", ["time_series", "cross_sectional"])
def test_sparse_or_constant_inputs_return_stable_not_evaluable_reason(mode):
    timestamps = pd.date_range("2025-01-01", periods=2, freq="D", tz="UTC")
    symbols = ["BTCUSDT"] if mode == "time_series" else [f"A{i}" for i in range(5)]
    run = _evaluation_run(mode, timestamps, symbols)
    index = pd.MultiIndex.from_product([timestamps, symbols], names=["open_time", "symbol"])
    result = evaluate_v2(pd.Series(1.0, index=index), run, identity="sparse")
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"]


def test_cost_diagnostic_records_assumptions_and_turnover():
    timestamps = pd.date_range("2025-01-01", periods=8, freq="D", tz="UTC")
    symbols = [f"A{i}" for i in range(5)]
    run = _evaluation_run("cross_sectional", timestamps, symbols)
    index = pd.MultiIndex.from_product([timestamps, symbols], names=["open_time", "symbol"])
    result = evaluate_v2(pd.Series(list(range(5)) * len(timestamps), index=index), run, identity="cost")
    diagnostic = result["metrics"]["cost_diagnostic"]
    assert diagnostic["assumption_only"] is True
    assert diagnostic["turnover"] is not None
    assert "executable PnL" in diagnostic["interpretation"]
    assert not ({"p_value", "confidence_interval", "bootstrap", "hac", "mde"} & set(result["metrics"]))


def test_low_valid_coverage_is_not_evaluable():
    timestamps = pd.date_range("2025-01-01", periods=8, freq="D", tz="UTC")
    run = _evaluation_run("time_series", timestamps, ["BTCUSDT"])
    index = pd.MultiIndex.from_tuples([(stamp, "BTCUSDT") for stamp in timestamps], names=["open_time", "symbol"])
    factor = pd.Series([1.0, 2.0, 3.0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan")], index=index)
    result = evaluate_v2(factor, run, identity="low-coverage")
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "INSUFFICIENT_COVERAGE"
