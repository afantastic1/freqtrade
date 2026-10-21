"""Research proxy returns for a point-in-time market panel."""

import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.timeframe import timeframe_to_seconds


CROSS_SECTIONAL_LABEL_VERSION = "cross-sectional-research-proxy-v1"


def cross_sectional_labels(
    panel: MarketPanel, membership: pd.Series, *, timeframe: str, horizon: int
) -> pd.DataFrame:
    if type(horizon) is not int or horizon < 1:
        raise ValueError("horizon must be a positive integer")
    if not membership.index.equals(panel.frame.index) or membership.isna().any():
        raise ValueError("membership must align with panel")
    step = pd.Timedelta(seconds=timeframe_to_seconds(timeframe))
    pieces = []
    for _, group in panel.frame.groupby(level="symbol", sort=False):
        time = group.index.get_level_values("open_time")
        entry = group["open"].shift(-1)
        exit_price = group["close"].shift(-horizon)
        valid = pd.Series(True, index=group.index)
        for lag in range(1, horizon + 1):
            future = pd.Series(time, index=group.index).shift(-lag)
            valid &= future.eq(time + lag * step)
        raw = (exit_price / entry - 1.0).where(valid)
        label_time = group["available_time"].shift(-horizon).where(raw.notna())
        entry_time = pd.Series(time, index=group.index).shift(-1)
        pieces.append(pd.DataFrame({
            "decision_time": group["available_time"],
            "entry_time": entry_time,
            "label_available_time": label_time,
            "raw_return": raw,
            "entry_time_feasible": (group["available_time"] <= entry_time).fillna(False),
        }, index=group.index))
    result = pd.concat(pieces).reindex(panel.frame.index)
    eligible = result["raw_return"].where(membership.astype(bool))
    grouped = eligible.groupby(level="open_time")
    mean = grouped.transform("mean")
    count = grouped.transform("count")
    result["relative_return"] = (eligible - mean).where(count >= 2)
    result["universe_size_with_label"] = count.astype(int)
    result["label_kind"] = "research_proxy_next_open_to_horizon_close"
    return result
