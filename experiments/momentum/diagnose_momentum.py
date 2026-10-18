
import numpy as np
import pandas as pd

PATH = "user_data/research/alpha_lab/momentum_4h_research.csv"


def main():
    df = pd.read_csv(PATH)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["factor", "label"]).copy()

    df["year"] = df["open_time"].dt.year

    # 与上一轮实验保持一致：按年度计算五分位
    df["quintile"] = df.groupby("year")["factor"].transform(
        lambda s: pd.qcut(
            s.rank(method="first"),
            q=5,
            labels=False,
        )
    )

    print("=== RETURN DISTRIBUTION (bps) ===")

    for year, group in df.groupby("year"):
        print(f"\nYEAR {year}")

        for q in (0, 4):
            y = group.loc[
                group["quintile"] == q, "label"
            ] * 10000

            lo, hi = y.quantile([0.01, 0.99])

            # 这里只做极端值影响诊断，不是可交易收益
            winsorized = y.clip(lower=lo, upper=hi)

            print(
                f"Q{q + 1}: "
                f"N={len(y)} "
                f"mean={y.mean():+.2f} "
                f"median={y.median():+.2f} "
                f"trim_mean={y.sort_values().iloc[
                    int(len(y)*0.01):int(len(y)*0.99)
                ].mean():+.2f} "
                f"winsor_mean={winsorized.mean():+.2f} "
                f"p01={lo:+.2f} "
                f"p99={hi:+.2f}"
            )

    print("\n=== TAIL CONTRIBUTION ===")

    for year, group in df.groupby("year"):
        print(f"\nYEAR {year}")

        for q in (0, 4):
            y = group.loc[
                group["quintile"] == q, "label"
            ] * 10000

            threshold = y.quantile(0.99)
            tail = y[y >= threshold]

            print(
                f"Q{q + 1} top 1%: "
                f"mean={tail.mean():+.2f} bps, "
                f"count={len(tail)}"
            )


if __name__ == "__main__":
    main()
