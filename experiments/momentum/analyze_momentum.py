
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


INPUT = Path(
    "user_data/research/alpha_lab/"
    "momentum_4h_research.csv"
)


def main():
    df = pd.read_csv(INPUT)

    df["open_time"] = pd.to_datetime(
        df["open_time"], utc=True
    )
    df["label_end_time"] = pd.to_datetime(
        df["label_end_time"], utc=True
    )

    df = df.replace(
        [np.inf, -np.inf], np.nan
    ).dropna(subset=["factor", "label"])

    # 1. 分月 IC
    df["month"] = df["open_time"].dt.strftime("%Y-%m")

    rows = []

    for month, group in df.groupby("month"):
        if len(group) < 100:
            continue

        ic = group["factor"].corr(group["label"])

        rank_ic = spearmanr(
            group["factor"],
            group["label"],
        ).statistic

        rows.append({
            "month": month,
            "n": len(group),
            "ic": ic,
            "rank_ic": rank_ic,
        })

    monthly = pd.DataFrame(rows)

    print("\n=== MONTHLY IC ===")
    print(monthly.to_string(index=False))

    print("\n=== IC STABILITY ===")
    print("Months:", len(monthly))

    print(
        "Negative RankIC ratio:",
        f"{(monthly['rank_ic'] < 0).mean():.2%}",
    )

    print(
        "Mean monthly RankIC:",
        f"{monthly['rank_ic'].mean():+.6f}",
    )

    print(
        "Median monthly RankIC:",
        f"{monthly['rank_ic'].median():+.6f}",
    )

    # 2. 按年份分别分组，避免市场分布变化
    #    导致三年数据混合后的分组失真
    df["year"] = df["open_time"].dt.year

    # 每年分为 5 组，0 是动量最低组
    df["quintile"] = df.groupby("year")["factor"].transform(
        lambda s: pd.qcut(
            s.rank(method="first"),
            q=5,
            labels=False,
        )
    )

    grouped = (
        df.groupby(["year", "quintile"])["label"]
        .agg(["mean", "median", "count"])
        .reset_index()
    )

    print("\n=== QUINTILE RETURNS (bps) ===")

    grouped["mean_bps"] = grouped["mean"] * 10000
    grouped["median_bps"] = grouped["median"] * 10000

    print(
        grouped[
            ["year", "quintile", "mean_bps",
             "median_bps", "count"]
        ].to_string(index=False)
    )

    # 3. Q1 - Q5 的条件收益差
    pivot = grouped.pivot(
        index="year",
        columns="quintile",
        values="mean_bps",
    )

    spread = pivot[0] - pivot[4]

    print("\n=== LOW MOMENTUM - HIGH MOMENTUM ===")

    for year, value in spread.items():
        print(f"{year}: {value:+.4f} bps")

    # 4. 保存结果
    output = INPUT.parent
    monthly.to_csv(
        output / "momentum_monthly_ic.csv",
        index=False,
    )
    grouped.to_csv(
        output / "momentum_quintiles.csv",
        index=False,
    )


if __name__ == "__main__":
    main()
