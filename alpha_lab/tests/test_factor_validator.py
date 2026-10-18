
import numpy as np
import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.factors.momentum import momentum
from alpha_lab.validation.factor_validator import FactorValidator


def make_market(n=100):
    times = pd.date_range(
        "2024-01-01",
        periods=n,
        freq="1h",
        tz="UTC",
    )
    close = 40000 + np.arange(n, dtype=float) * 3

    frame = pd.DataFrame({
        "open_time": times,
        "close_time": times + pd.Timedelta(hours=1),
        "available_time": (
            times + pd.Timedelta(hours=1, seconds=1)
        ),
        "open": close,
        "high": close + 5,
        "low": close - 5,
        "close": close,
        "volume": np.full(n, 100.0),
    })

    return MarketData(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        frame=frame,
    )


def test_valid_momentum():
    market = make_market()

    validator = FactorValidator()

    report = validator.validate(
        market,
        momentum,
        {"periods": 4},
    )

    assert report.passed, report.errors
    assert report.valid_count == 96


def test_future_factor_rejected():
    market = make_market()

    def future_factor(data):
        return data.frame["close"].shift(-1)

    validator = FactorValidator()

    report = validator.validate(market, future_factor)

    assert not report.passed
    assert any(
        "Prefix invariance" in error
        or "AssertionError" in error
        for error in report.errors
    )


def test_wrong_output_type_rejected():
    market = make_market()

    def bad_factor(data):
        return [1.0] * len(data.frame)

    report = FactorValidator().validate(
        market, bad_factor
    )

    assert not report.passed
    assert any("pd.Series" in e for e in report.errors)


def test_input_mutation_rejected():
    market = make_market()

    def mutating_factor(data):
        data.frame["close"] *= 2
        return data.frame["close"]

    report = FactorValidator().validate(
        market, mutating_factor
    )

    assert not report.passed


def test_low_coverage_rejected():
    market = make_market()

    def mostly_nan(data):
        result = pd.Series(np.nan, index=data.frame.index)
        result.iloc[-5:] = 1.0
        return result

    report = FactorValidator().validate(
        market, mostly_nan
    )

    assert not report.passed
    assert any("coverage" in e for e in report.errors)
