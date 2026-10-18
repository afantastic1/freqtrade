
from dataclasses import dataclass
import pandas as pd

from alpha_lab.data.dataset import MarketData
from freqtrade.exchange import timeframe_to_seconds


@dataclass(frozen=True)
class DataQualityReport:
    symbol: str
    timeframe: str
    actual_bars: int
    expected_bars: int
    missing_bars: int
    coverage: float
    first_open: pd.Timestamp
    last_open: pd.Timestamp
    gaps: pd.DataFrame


def check_quality(data: MarketData) -> DataQualityReport:
    """
    检查固定周期 OHLCV 数据的时间连续性。

    不插值、不填充、不修改原始行情。
    """
    df = data.frame
    if df.empty:
        raise ValueError("Cannot check empty MarketData")

    seconds = timeframe_to_seconds(data.timeframe)
    step = pd.Timedelta(seconds=seconds)

    opens = df["open_time"]

    # 检查每根 K 线是否落在周期网格上
    timestamps_ns = opens.astype("int64")
    step_ns = step.value

    # 使用 Pandas 时间运算，避免 ns/us 精度不一致
    epoch = pd.Timestamp("1970-01-01T00:00:00Z")

    offsets = opens - epoch

    if (offsets % step != pd.Timedelta(0)).any():
        raise ValueError("Candles are not aligned to timeframe")
    if (opens.diff().dropna() < step).any():
        raise ValueError("Candles overlap or timeframe is inconsistent")

    # 相邻 K 线的开盘时间差
    delta = opens.diff()

    # 大于一个周期，说明存在缺口
    gap_mask = delta > step

    gaps = pd.DataFrame({
        "previous_open": opens.shift(1)[gap_mask],
        "next_open": opens[gap_mask],
    })

    if not gaps.empty:
        gaps = gaps.copy()
        gaps["missing_bars"] = (
            (
                gaps["next_open"] - gaps["previous_open"]
            ) // step - 1
        ).astype(int)

    gaps = gaps.reset_index(drop=True)

    missing = int(gaps["missing_bars"].sum()) if not gaps.empty else 0

    expected = int(
        (opens.iloc[-1] - opens.iloc[0]) // step
    ) + 1

    actual = len(df)
    coverage = actual / expected

    return DataQualityReport(
        symbol=data.symbol,
        timeframe=data.timeframe,
        actual_bars=actual,
        expected_bars=expected,
        missing_bars=missing,
        coverage=coverage,
        first_open=opens.iloc[0],
        last_open=opens.iloc[-1],
        gaps=gaps,
    )
