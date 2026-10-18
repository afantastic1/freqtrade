
import numpy as np
import pandas as pd

PATH = (
    "user_data/research/alpha_lab/"
    "rolling_logistic_predictions.csv"
)


def main():
    df = pd.read_csv(PATH)
    df["open_time"] = pd.to_datetime(
        df["open_time"], utc=True
    )

    required = ["target", "probability", "baseline"]
    if df[required].isna().any().any():
        raise ValueError("Missing prediction values")

    y = df["target"].to_numpy(dtype=float)
    p = df["probability"].to_numpy(dtype=float)
    b = df["baseline"].to_numpy(dtype=float)

    if not np.isin(y, [0, 1]).all():
        raise ValueError("Targets must be binary")

    if ((p < 0) | (p > 1) | (b < 0) | (b > 1)).any():
        raise ValueError("Invalid probabilities")

    # 正数代表模型比基线更好
    df["brier_gain"] = (b - y)**2 - (p - y)**2

    eps = 1e-12
    p = np.clip(p, eps, 1 - eps)
    b = np.clip(b, eps, 1 - eps)

    model_loss = -(y*np.log(p) + (1-y)*np.log1p(-p))
    base_loss = -(y*np.log(b) + (1-y)*np.log1p(-b))

    df["logloss_gain"] = base_loss - model_loss

    print("=== YEARLY IMPROVEMENT ===")
    df["year"] = df["open_time"].dt.year

    print(
        df.groupby("year")[
            ["brier_gain", "logloss_gain"]
        ].agg(["mean", "count"])
    )

    print("\n=== MONTHLY IMPROVEMENT ===")
    monthly = df.groupby("month")[
        ["brier_gain", "logloss_gain"]
    ].mean()

    print(monthly.to_string())

    print("\nPositive Brier months:",
          int((monthly["brier_gain"] > 0).sum()),
          "/", len(monthly))

    # 按连续日历周重采样，保留周内样本相关性。
    # 仍然不能保证周与周之间严格独立。
    df["week"] = (
        df["open_time"].dt.tz_localize(None)
        .dt.to_period("W-SUN")
        .astype(str)
    )

    weekly = df.groupby("week").agg(
        n=("target", "size"),
        brier_sum=("brier_gain", "sum"),
        logloss_sum=("logloss_gain", "sum"),
    )

    rng = np.random.default_rng(42)
    n_weeks = len(weekly)
    n_boot = 2000

    sampled = rng.integers(
        0, n_weeks, size=(n_boot, n_weeks)
    )

    n = weekly["n"].to_numpy()
    brier = weekly["brier_sum"].to_numpy()
    logloss = weekly["logloss_sum"].to_numpy()

    sampled_n = n[sampled].sum(axis=1)

    brier_boot = (
        brier[sampled].sum(axis=1) / sampled_n
    )
    logloss_boot = (
        logloss[sampled].sum(axis=1) / sampled_n
    )

    print("\n=== WEEK BLOCK BOOTSTRAP ===")

    for name, values in [
        ("Brier improvement", brier_boot),
        ("LogLoss improvement", logloss_boot),
    ]:
        low, high = np.quantile(
            values, [0.025, 0.975]
        )
        print(
            f"{name}: "
            f"95% bootstrap interval "
            f"[{low:+.6f}, {high:+.6f}]"
        )


if __name__ == "__main__":
    main()
