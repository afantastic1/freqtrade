
import pandas as pd

from alpha_lab.data.timeframe import timeframe_to_seconds
from alpha_lab.data.dataset import MarketData


def momentum(
    data: MarketData,
    periods: int = 4,
) -> pd.Series:
    """
    计算过去 periods 根 K 线的动量。

    要求对应时间窗口内 K 线连续。
    缺失数据时返回 NaN。
    """
    if periods <= 0:
        raise ValueError("periods must be positive")

    df = data.frame

    step = pd.Timedelta(
        seconds=timeframe_to_seconds(data.timeframe)
    )

    # 基础动量
    result = df["close"] / df["close"].shift(periods) - 1.0

    # 标记不连续的时间间隔
    gaps = df["open_time"].diff().ne(step)

    # 第一个位置不存在前向时间间隔，不应视为缺口
    gaps.iloc[0] = False

    # periods=4 时，仅检查索引 1、2、3、4
    invalid = (
        gaps.rolling(
            window=periods,
            min_periods=periods,
        )
        .max()
        .fillna(1)
        .astype(bool)
    )

    result = result.mask(invalid)
    result.name = f"momentum_{periods}"

    return result
