from pathlib import Path

import pandas as pd

from freqtrade.data.history import load_pair_history
from freqtrade.configuration.timerange import TimeRange
from freqtrade.enums import CandleType

from alpha_lab.data.dataset import MarketData
from alpha_lab.data.timeframe import timeframe_to_seconds


def from_freqtrade_dataframe(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    availability_delay: pd.Timedelta = pd.Timedelta(seconds=1),
) -> MarketData:
    """
    将 Freqtrade OHLCV DataFrame 转换为 MarketData。

    Freqtrade 的 date 表示 K 线开盘时间。
    availability_delay 是显式的研究假设，不是实际测量延迟。
    """
    if "date" not in df.columns:
        raise ValueError("Missing Freqtrade date column")

    if availability_delay < pd.Timedelta(0):
        raise ValueError("availability_delay cannot be negative")

    result = df.copy(deep=True)

    # 严格要求带时区的 datetime
    dates = result["date"]

    if not isinstance(dates.dtype, pd.DatetimeTZDtype):
        raise ValueError("date must be timezone-aware datetime")

    if dates.isna().any():
        raise ValueError("date contains NaT")

    result["open_time"] = dates.dt.tz_convert("UTC")

    # 根据 Freqtrade 周期得到 K 线长度
    seconds = timeframe_to_seconds(timeframe)
    if seconds <= 0:
        raise ValueError("Invalid timeframe")

    result["close_time"] = (
        result["open_time"]
        + pd.Timedelta(seconds=seconds)
    )

    result["available_time"] = (
        result["close_time"]
        + availability_delay
    )# Freqtrade 的 date 通常表示 K 线开盘时间, 避免未来函数（Look-ahead Bias）。

    result = result.drop(columns=["date"])

    return MarketData(
        symbol=symbol,
        timeframe=timeframe,
        frame=result,
    )



def load_ohlcv(
    symbol: str,
    timeframe: str,
    datadir: str | Path,
    start: str,
    end: str,
    data_format: str = "feather",
    availability_delay: pd.Timedelta = pd.Timedelta(seconds=1),
) -> MarketData:
    """
    按时间范围读取 Freqtrade 历史行情。

    使用半开区间：
        [start, end)
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")

    if start_ts >= end_ts:
        raise ValueError("start must be before end")

    timerange = TimeRange(
        starttype="date",
        stoptype="date",
        startts=int(start_ts.timestamp()),
        stopts=int(end_ts.timestamp()),
    )

    candles = load_pair_history(
        pair=symbol,
        timeframe=timeframe,
        datadir=Path(datadir),
        timerange=timerange,
        data_format=data_format,
        candle_type=CandleType.FUTURES,
        fill_up_missing=False,
        drop_incomplete=False,
    )

    # 明确保证半开区间语义
    candles = candles.loc[
        (candles["date"] >= start_ts)
        & (candles["date"] < end_ts)
    ].copy()

    if candles.empty:
        raise ValueError("No OHLCV data found")

    return from_freqtrade_dataframe(
        candles,
        symbol=symbol,
        timeframe=timeframe,
        availability_delay=availability_delay,
    )
