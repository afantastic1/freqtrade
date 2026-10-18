
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.dataset import MarketData
from alpha_lab.data.quality import check_quality

import pandas as pd


def main():
    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        datadir="user_data/data/binance",
        data_format="feather",
    )

    # 只允许开发集进入研究
    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2025-01-01", tz="UTC")

    df = market.frame.loc[
        (market.frame["open_time"] >= start)
        & (market.frame["close_time"] <= end)
    ].copy()

    if df.empty:
        raise ValueError("No development data")

    development = MarketData(
        symbol=market.symbol,
        timeframe=market.timeframe,
        frame=df,
    )

    report = check_quality(development)

    print("Symbol:", report.symbol)
    print("Timeframe:", report.timeframe)
    print("Actual bars:", report.actual_bars)
    print("Expected bars:", report.expected_bars)
    print("Missing bars:", report.missing_bars)
    print("Coverage:", f"{report.coverage:.4%}")
    print("First open:", report.first_open)
    print("Last open:", report.last_open)

    print("\nFirst 3 candles:")
    print(development.frame.head(3))

    print("\nLast 3 candles:")
    print(development.frame.tail(3))

    print("\nGaps:")
    print(report.gaps.head(20))


if __name__ == "__main__":
    main()
