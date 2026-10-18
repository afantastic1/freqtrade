
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


def evaluate_ic(
    factor: pd.Series,
    label: pd.Series,
) -> dict:
    """
    评价单因子的预测能力。

    Pearson IC：线性相关性
    RankIC：排序相关性
    """
    if not factor.index.equals(label.index):
        raise ValueError("Factor and label indexes differ")

    df = pd.DataFrame({
        "factor": factor,
        "label": label,
    })

    df = df.replace([np.inf, -np.inf], np.nan).dropna()

    if len(df) < 3:
        raise ValueError("Not enough valid observations")

    if df["factor"].nunique() < 2:
        raise ValueError("Constant factor")

    if df["label"].nunique() < 2:
        raise ValueError("Constant label")

    ic = pearsonr(df["factor"], df["label"]).statistic
    rank_ic = spearmanr(df["factor"], df["label"]).statistic

    return {
        "ic": float(ic),
        "rank_ic": float(rank_ic),
        "n_obs": len(df),
    }
