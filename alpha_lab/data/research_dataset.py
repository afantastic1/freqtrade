
import numpy as np
import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.labels.forward_returns import forward_returns


def build_research_dataset(
    market: MarketData,
    features: pd.DataFrame,
    horizon: int = 4,
) -> pd.DataFrame:
    """
    将任意特征矩阵 X 与标签 y 组合。

    不计算因子，不依赖任何具体 Factor。
    """
    if not features.index.equals(market.frame.index):
        raise ValueError("Feature index mismatch")

    if not features.columns.is_unique:
        raise ValueError("Duplicate feature columns")

    if features.empty:
        raise ValueError("Empty feature matrix")

    labels = forward_returns(
        market,
        horizon=horizon,
    )

    metadata = pd.DataFrame({
        "open_time": market.frame["open_time"],
        "decision_time": labels["decision_time"],
        "label_end_time": labels["label_end_time"],
        "label_available_time": labels["label_available_time"],
        "reference_return": labels["reference_return"],
        "entry_time_feasible": labels["entry_time_feasible"],
    })

    reserved = set(metadata.columns) | {"target"}

    conflicts = reserved.intersection(features.columns)

    if conflicts:
        raise ValueError(
            f"Factor names conflict with metadata: {sorted(conflicts)}"
    )
    
    valid_label = (
    metadata["reference_return"].notna()
    & metadata["label_available_time"].notna()
    )

    metadata["target"] = pd.Series(
        pd.NA,
        index=metadata.index,
        dtype="Int64",
    )

    metadata.loc[valid_label, "target"] = (
        metadata.loc[valid_label, "reference_return"] > 0
    ).astype(int)

    # 对齐并组合
    return pd.concat(
        [
            metadata,
            features.replace(
                [np.inf, -np.inf],
                np.nan,
            ),
        ],
        axis=1,
    )
