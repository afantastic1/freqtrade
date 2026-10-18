
from pathlib import Path

import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.quality import check_quality
from alpha_lab.factors.momentum import momentum
from alpha_lab.labels.forward_returns import forward_returns
from alpha_lab.evaluation.ic import evaluate_ic


SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "1h"
MOMENTUM_PERIODS = 4
HORIZON = 4

DEV_START = pd.Timestamp("2022-01-01", tz="UTC")
DEV_END = pd.Timestamp("2025-01-01", tz="UTC")


def main():
    # 1. 加载行情
    market = load_ohlcv(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        datadir="user_data/data/binance",
        data_format="feather",
    )

    # 2. 限定开发集
    df = market.frame.loc[
        (market.frame["open_time"] >= DEV_START)
        & (market.frame["close_time"] <= DEV_END)
    ].copy().reset_index(drop=True)

    if df.empty:
        raise ValueError("No development data found")

    data = MarketData(
        symbol=market.symbol,
        timeframe=market.timeframe,
        frame=df,
    )

    # 3. 数据质量检查
    quality = check_quality(data)

    print("\n=== DATA QUALITY ===")
    print("First:", quality.first_open)
    print("Last:", quality.last_open)
    print("Bars:", quality.actual_bars)
    print("Missing:", quality.missing_bars)
    print("Coverage:", f"{quality.coverage:.4%}")

    # 避免把局部完整的数据误认为全区间完整
    expected_first = DEV_START
    expected_last = DEV_END - pd.Timedelta(hours=1)

    if (
        quality.first_open != expected_first
        or quality.last_open != expected_last
    ):
        raise ValueError(
            "Development data does not cover the full "
            "2022-2024 period"
        )

    if quality.missing_bars > 0:
        print(
            "WARNING: Missing candles detected; "
            "factor and label windows must exclude gaps."
        )

    # 4. 计算因子
    factor = momentum(
        data,
        periods=MOMENTUM_PERIODS,
    )

    # 5. 计算未来收益标签
    labels = forward_returns(
        data,
        horizon=HORIZON,
    )

    # 6. 构建研究样本
    research = pd.DataFrame({
        "open_time": data.frame["open_time"],
        "factor": factor,
        "label": labels["reference_return"],
        "label_end_time": labels["label_end_time"],
        "entry_time_feasible": labels["entry_time_feasible"],
    })

    research = research.dropna(
        subset=["factor", "label", "label_end_time"]
    ).copy()

    print("\n=== SAMPLE ===")
    print(research.head(10).to_string(index=False))
    print("Valid samples:", len(research))

    # 7. 全开发集 IC
    result = evaluate_ic(
        research["factor"],
        research["label"],
    )

    print("\n=== OVERALL IC ===")
    print(f"Pearson IC: {result['ic']:.6f}")
    print(f"RankIC:     {result['rank_ic']:.6f}")
    print(f"N:          {result['n_obs']}")

    # 8. 按年份观察稳定性
    research["year"] = research["open_time"].dt.year

    print("\n=== YEARLY IC ===")

    for year, group in research.groupby("year"):
        # 年度评价不允许使用标签结束于次年的样本
        year_end = pd.Timestamp(
            f"{int(year) + 1}-01-01",
            tz="UTC",
        )
        group = group.loc[
            group["label_end_time"] <= year_end
        ]

        if len(group) < 30:
            continue

        yearly = evaluate_ic(
            group["factor"],
            group["label"],
        )

        print(
            f"{year}: "
            f"IC={yearly['ic']:+.6f}, "
            f"RankIC={yearly['rank_ic']:+.6f}, "
            f"N={yearly['n_obs']}"
        )

    # 9. 检查预测标签的时间可执行性
    feasible_rate = research["entry_time_feasible"].mean()

    print("\n=== EXECUTION TIMING ===")
    print(f"Next-open time feasible: {feasible_rate:.2%}")
    print("Reference returns are NOT executable trade PnL.")

    # 10. 保存研究数据
    output = Path("user_data/research/alpha_lab")
    output.mkdir(parents=True, exist_ok=True)

    path = output / "momentum_4h_research.csv"

    research.to_csv(path, index=False)

    print("\nSaved:", path)


if __name__ == "__main__":
    main()
