
import pandas as pd
import pytest

from alpha_lab.data.loader import from_freqtrade_dataframe


def make_freqtrade_data():
    return pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-01T10:00:00Z",
            "2024-01-01T11:00:00Z",
        ]),
        "open": [42000.0, 42100.0],
        "high": [42200.0, 42300.0],
        "low": [41900.0, 42000.0],
        "close": [42100.0, 42200.0],
        "volume": [125.6, 130.2],
    })


def test_freqtrade_conversion():
    data = from_freqtrade_dataframe(
        df=make_freqtrade_data(),
        symbol="BTC/USDT:USDT",
        timeframe="1h",
    )

    assert len(data) == 2

    row = data.frame.iloc[0]

    assert row["open_time"] == pd.Timestamp(
        "2024-01-01T10:00:00Z"
    )

    assert row["close_time"] == pd.Timestamp(
        "2024-01-01T11:00:00Z"
    )

    assert row["available_time"] == pd.Timestamp(
        "2024-01-01T11:00:01Z"
    )


def test_15m_conversion():
    data = from_freqtrade_dataframe(
        df=make_freqtrade_data(),
        symbol="BTC/USDT:USDT",
        timeframe="15m",
    )

    row = data.frame.iloc[0]

    assert row["close_time"] == pd.Timestamp(
        "2024-01-01T10:15:00Z"
    )


def test_negative_delay():
    with pytest.raises(
        ValueError,
        match="availability_delay cannot be negative"
    ):
        from_freqtrade_dataframe(
            df=make_freqtrade_data(),
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            availability_delay=pd.Timedelta(seconds=-1),
        )


def test_source_not_modified():
    original = make_freqtrade_data()
    before = original.copy(deep=True)

    from_freqtrade_dataframe(
        original,
        "BTC/USDT:USDT",
        "1h",
    )

    pd.testing.assert_frame_equal(original, before)
