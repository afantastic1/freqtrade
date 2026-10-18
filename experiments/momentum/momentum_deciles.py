
import pandas as pd
import numpy as np

PATH = "user_data/research/alpha_lab/momentum_4h_research.csv"


def main():
    df = pd.read_csv(PATH)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["factor", "label"]).copy()

    df["year"] = df["open_time"].dt.year

    # 按年度做十等分，仅用于开发集描述性分析
    df["decile"] = df.groupby("year")["factor"].transform(
        lambda s: pd.qcut(
            s.rank(method="first"),
            10,
            labels=False,
        )
    )

    result = (
        df.groupby(["year", "decile"])["label"]
        .agg(
            n="count",
            mean="mean",
            median="median",
            positive_rate=lambda x: (x > 0).mean(),
            p05=lambda x: x.quantile(0.05),
            p95=lambda x: x.quantile(0.95),
        )
        .reset_index()
    )

    for col in ["mean", "median", "p05", "p95"]:
        result[col] *= 10000

    print("\n=== MOMENTUM DECILES ===")
    print(result.to_string(index=False))

    print("\n=== POSITIVE RETURN RATE ===")

    pivot = result.pivot(
        index="decile",
        columns="year",
        values="positive_rate",
    )

    print((pivot * 100).round(2).to_string())

    output = (
        "user_data/research/alpha_lab/"
        "momentum_deciles.csv"
    )

    result.to_csv(output, index=False)
    print("\nSaved:", output)


if __name__ == "__main__":
    main()
