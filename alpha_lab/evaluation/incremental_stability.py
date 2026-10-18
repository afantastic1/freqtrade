
import numpy as np
import pandas as pd


EPS = 1e-15


def evaluate_stability(
    timestamps: pd.Series,
    y_true: pd.Series,
    baseline_prob: pd.Series,
    combined_prob: pd.Series,
    *,
    bootstrap_samples: int = 1000,
    block_size: int = 24,
    random_state: int = 42,
) -> dict:
    """
    同一批预测样本上的配对增量评价。

    按月计算 Log Loss/Brier 改善，并使用
    Moving Block Bootstrap 估计整体改善的不确定性。

    不重新训练模型，不使用未来数据拟合预测器。
    """
    if bootstrap_samples < 1 or block_size < 1:
        raise ValueError("Invalid bootstrap parameters")

    if not (
        len(timestamps)
        == len(y_true)
        == len(baseline_prob)
        == len(combined_prob)
    ):
        raise ValueError("Input lengths do not match")

    df = pd.DataFrame({
        "time": pd.to_datetime(
            timestamps.to_numpy(),
            utc=True,
        ),
        "y": np.asarray(y_true, dtype=float),
        "p_base": np.asarray(baseline_prob, dtype=float),
        "p_comb": np.asarray(combined_prob, dtype=float),
    }).sort_values("time").reset_index(drop=True)

    if df.empty:
        raise ValueError("Empty predictions")

    if df["time"].isna().any():
        raise ValueError("Invalid timestamps")

    if df["time"].duplicated().any():
        raise ValueError("Duplicate prediction timestamps")

    if not np.isfinite(
        df[["y", "p_base", "p_comb"]].to_numpy()
    ).all():
        raise ValueError("Non-finite prediction data")

    y = df["y"].to_numpy()
    pb = df["p_base"].to_numpy()
    pc = df["p_comb"].to_numpy()

    if not np.isin(y, [0.0, 1.0]).all():
        raise ValueError("Labels must be binary")

    if (
        (pb < 0).any() or (pb > 1).any()
        or (pc < 0).any() or (pc > 1).any()
    ):
        raise ValueError("Probabilities must be in [0, 1]")

    pb_clip = np.clip(pb, EPS, 1 - EPS)
    pc_clip = np.clip(pc, EPS, 1 - EPS)

    def logloss_per_sample(p):
        return -(y * np.log(p) + (1 - y) * np.log1p(-p))

    base_loss = logloss_per_sample(pb_clip)
    comb_loss = logloss_per_sample(pc_clip)

    # 正值代表组合模型减少了损失
    logloss_gain = base_loss - comb_loss
    brier_gain = (pb - y) ** 2 - (pc - y) ** 2

    df["logloss_improvement"] = logloss_gain
    df["brier_improvement"] = brier_gain
    df["month"] = df["time"].dt.strftime("%Y-%m")

    monthly = (
        df.groupby("month", sort=True)
        .agg(
            n=("y", "size"),
            logloss_improvement=(
                "logloss_improvement", "mean"
            ),
            brier_improvement=(
                "brier_improvement", "mean"
            ),
        )
        .reset_index()
    )

    def block_bootstrap_ci(values):
        values = np.asarray(values, dtype=float)
        n = len(values)

        if n < 2:
            raise ValueError("Not enough samples")

        if block_size > n:
            raise ValueError("block_size exceeds sample count")

        rng = np.random.default_rng(random_state)
        n_blocks = int(np.ceil(n / block_size))
        max_start = n - block_size

        means = np.empty(bootstrap_samples)

        for i in range(bootstrap_samples):
            starts = rng.integers(
                0,
                max_start + 1,
                size=n_blocks,
            )

            indices = np.concatenate([
                np.arange(s, s + block_size)
                for s in starts
            ])[:n]

            means[i] = values[indices].mean()

        lower, upper = np.quantile(
            means,
            [0.025, 0.975],
        )

        return {
            "mean": float(values.mean()),
            "ci_95_lower": float(lower),
            "ci_95_upper": float(upper),
            "bootstrap_positive_ratio": float(
                np.mean(means > 0)
            ),
        }

    return {
        "n": len(df),
        "monthly": monthly.to_dict(orient="records"),
        "summary": {
            "months": len(monthly),
            "positive_logloss_months": int(
                (monthly["logloss_improvement"] > 0).sum()
            ),
            "positive_brier_months": int(
                (monthly["brier_improvement"] > 0).sum()
            ),
            "logloss": block_bootstrap_ci(logloss_gain),
            "brier": block_bootstrap_ci(brier_gain),
        },
        "bootstrap": {
            "samples": bootstrap_samples,
            "block_size": block_size,
            "random_state": random_state,
        },
    }
