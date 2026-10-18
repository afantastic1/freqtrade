
import numpy as np
import pandas as pd
import pytest

from alpha_lab.data.dataset import MarketData


def make_data():
    return pd.DataFrame({
        "open_time": pd.to_datetime([
            "2024-01-01T10:00:00Z",
            "2024-01-01T11:00:00Z",
        ]),
        "close_time": pd.to_datetime([
            "2024-01-01T11:00:00Z",
            "2024-01-01T12:00:00Z",
        ]),
        "available_time": pd.to_datetime([
            "2024-01-01T11:00:02Z",
            "2024-01-01T12:00:02Z",
        ]),
        "open": [42000.0, 42100.0],
        "high": [42200.0, 42300.0],
        "low": [41900.0, 42000.0],
        "close": [42100.0, 42200.0],
        "volume": [125.6, 130.2],
    })


def build(df=None):
    return MarketData(
        symbol="BTCUSDT",
        timeframe="1h",
        frame=make_data() if df is None else df,
    )


# ---------- 基础数据校验 ----------

def test_valid_market_data():
    data = build()

    assert len(data) == 2
    assert data.symbol == "BTCUSDT"
    assert data.timeframe == "1h"


def test_missing_column():
    df = make_data().drop(columns=["high"])

    with pytest.raises(ValueError, match="Missing columns"):
        build(df)


def test_invalid_high():
    df = make_data()
    df.loc[0, "high"] = 41000

    with pytest.raises(
    ValueError,
    match="Invalid OHLC price relationship"
    ):
        build(df)


def test_invalid_low():
    df = make_data()
    df.loc[0, "low"] = 43000

    with pytest.raises(
    ValueError,
    match="Invalid OHLC price relationship"
    ):
        build(df)



def test_low_above_open():
    df = make_data()

    # Low 高于 Open，但仍然低于 High
    df.loc[0, "low"] = 42050

    with pytest.raises(
    ValueError,
    match="Invalid OHLC price relationship"
    ):
        build(df)



def test_duplicate_timestamp():
    df = make_data()
    df.loc[1, "open_time"] = df.loc[0, "open_time"]

    with pytest.raises(ValueError, match="Duplicate open_time"):
        build(df)


def test_unsorted_timestamp():
    df = make_data().iloc[::-1].reset_index(drop=True)

    with pytest.raises(ValueError, match="Open time must be monotonic"):
        build(df)


def test_future_availability():
    df = make_data()

    df.loc[0, "available_time"] = pd.Timestamp(
        "2024-01-01T10:30:00Z"
    )

    with pytest.raises(ValueError, match="Data available before close"):
        build(df)


def test_invalid_candle_time():
    df = make_data()

    df.loc[0, "close_time"] = df.loc[0, "open_time"]

    with pytest.raises(ValueError, match="Invalid candle time range"):
        build(df)


def test_zero_volume_is_valid():
    df = make_data()
    df.loc[0, "volume"] = 0

    data = build(df)

    assert data.frame.loc[0, "volume"] == 0


def test_negative_volume():
    df = make_data()
    df.loc[0, "volume"] = -1

    with pytest.raises(ValueError, match="volume must be non-negative"):
        build(df)


def test_infinite_price():
    df = make_data()
    df.loc[0, "close"] = np.inf

    with pytest.raises(ValueError, match="Invalid numeric value"):
        build(df)


def test_missing_timezone():
    df = make_data()

    df["open_time"] = df["open_time"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="must include timezone"):
        build(df)


def test_input_isolation():
    df = make_data()
    original = df.copy(deep=True)

    data = build(df)

    data.frame.loc[0, "close"] = 99999

    pd.testing.assert_frame_equal(df, original)


# ---------- Point-in-Time ----------

def test_asof_before_availability():
    data = build()

    history = data.asof("2024-01-01T11:00:01Z")

    assert len(history) == 0


def test_asof_at_availability():
    data = build()

    history = data.asof("2024-01-01T11:00:02Z")

    assert len(history) == 1
    assert history.frame.iloc[0]["close"] == 42100.0


def test_asof_all_available():
    data = build()

    history = data.asof("2024-01-01T12:00:02Z")

    assert len(history) == 2


def test_asof_rejects_naive_time():
    data = build()

    with pytest.raises(ValueError, match="must include timezone"):
        data.asof("2024-01-01 11:00:02")


def test_asof_does_not_modify_original():
    data = build()

    history = data.asof("2024-01-01T11:00:02Z")

    history.frame.loc[0, "close"] = 12345

    assert data.frame.loc[0, "close"] == 42100.0
