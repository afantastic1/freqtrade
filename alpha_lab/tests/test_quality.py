
import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.data.quality import check_quality


def make_market_data(hours=(0, 1, 2, 3)):
    base = pd.Timestamp("2024-01-01T00:00:00Z")

    rows = []
    for hour in hours:
        start = base + pd.Timedelta(hours=hour)
        end = start + pd.Timedelta(hours=1)

        rows.append({
            "open_time": start,
            "close_time": end,
            "available_time": end + pd.Timedelta(seconds=1),
            "open": 42000.0,
            "high": 42100.0,
            "low": 41900.0,
            "close": 42000.0,
            "volume": 100.0,
        })

    return MarketData(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        frame=pd.DataFrame(rows),
    )


def test_complete_data():
    data = make_market_data((0, 1, 2, 3))

    report = check_quality(data)

    assert report.actual_bars == 4
    assert report.expected_bars == 4
    assert report.missing_bars == 0
    assert report.coverage == 1.0


def test_one_missing_candle():
    data = make_market_data((0, 1, 3))

    report = check_quality(data)

    assert report.actual_bars == 3
    assert report.expected_bars == 4
    assert report.missing_bars == 1
    assert report.coverage == 0.75

    assert len(report.gaps) == 1
    assert report.gaps.iloc[0]["missing_bars"] == 1


def test_multiple_missing_candles():
    data = make_market_data((0, 1, 5))

    report = check_quality(data)

    assert report.actual_bars == 3
    assert report.expected_bars == 6
    assert report.missing_bars == 3
    assert report.coverage == 0.5


def test_quality_does_not_modify_data():
    data = make_market_data((0, 1, 3))
    original = data.frame.copy(deep=True)

    check_quality(data)

    pd.testing.assert_frame_equal(data.frame, original)

def test_microsecond_timestamp_precision():
    data = make_market_data((0, 1, 2, 3))

    df = data.frame.copy()

    for col in ("open_time", "close_time", "available_time"):
        df[col] = df[col].astype("datetime64[us, UTC]")

    microsecond_data = MarketData(
        symbol=data.symbol,
        timeframe=data.timeframe,
        frame=df,
    )

    report = check_quality(microsecond_data)

    assert report.coverage == 1.0
    assert report.missing_bars == 0