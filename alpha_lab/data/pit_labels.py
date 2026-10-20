"""Point-in-time-safe label helpers used by Dataset Builder v2."""

from __future__ import annotations

import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.data.market_panel import MarketPanel
from freqtrade.exchange import timeframe_to_seconds


def forward_returns_v2(data: MarketData, horizon: int) -> pd.DataFrame:
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("horizon must be positive integer")
    frame = data.frame
    step = pd.Timedelta(seconds=timeframe_to_seconds(data.timeframe))
    entry = frame.open.shift(-1)
    exit_price = frame.close.shift(-horizon)
    value = exit_price / entry - 1.0
    gaps = frame.open_time.diff().ne(step)
    future_gap = pd.Series(False, index=frame.index)
    for lag in range(1, horizon + 1):
        future_gap |= gaps.shift(-lag).fillna(True).astype(bool)
    value = value.mask(future_gap)
    valid = value.notna()
    label_end = frame.close_time.shift(-horizon).where(valid)
    label_available = pd.concat(
        [frame.available_time.shift(-1), frame.available_time.shift(-horizon)], axis=1
    ).max(axis=1).where(valid)
    next_open = frame.open_time.shift(-1)
    return pd.DataFrame({
        "decision_time": frame.available_time,
        "entry_time": next_open,
        "label_end_time": label_end,
        "label_available_time": label_available,
        "reference_return": value,
        "entry_time_feasible": (frame.available_time <= next_open).fillna(False),
    }, index=frame.index)


def cross_sectional_labels_v2(
    panel: MarketPanel, membership: pd.Series, *, timeframe: str, horizon: int
) -> pd.DataFrame:
    if type(horizon) is not int or horizon < 1:
        raise ValueError("horizon must be a positive integer")
    if not membership.index.equals(panel.frame.index) or membership.isna().any():
        raise ValueError("membership must align with panel")
    step = pd.Timedelta(seconds=timeframe_to_seconds(timeframe))
    pieces = []
    for _, group in panel.frame.groupby(level="symbol", sort=False):
        times = group.index.get_level_values("open_time")
        entry = group.open.shift(-1)
        exit_price = group.close.shift(-horizon)
        continuous = pd.Series(True, index=group.index)
        for lag in range(1, horizon + 1):
            future = pd.Series(times, index=group.index).shift(-lag)
            continuous &= future.eq(times + lag * step)
        raw = (exit_price / entry - 1.0).where(continuous)
        valid = raw.notna()
        available = pd.concat(
            [group.available_time.shift(-1), group.available_time.shift(-horizon)], axis=1
        ).max(axis=1).where(valid)
        label_end = group.close_time.shift(-horizon).where(valid)
        entry_time = pd.Series(times, index=group.index).shift(-1)
        pieces.append(pd.DataFrame({
            "decision_time": group.available_time,
            "entry_time": entry_time,
            "label_end_time": label_end,
            "label_available_time": available,
            "raw_return": raw,
            "entry_time_feasible": (group.available_time <= entry_time).fillna(False),
        }, index=group.index))
    result = pd.concat(pieces).reindex(panel.frame.index)
    eligible = result.raw_return.where(membership.astype(bool))
    grouped = eligible.groupby(level="open_time")
    mean = grouped.transform("mean")
    count = grouped.transform("count")
    result["relative_return"] = (eligible - mean).where(count >= 2)
    result["universe_size_with_label"] = count.astype(int)
    result["label_kind"] = "research_proxy_next_open_to_horizon_close"
    return result


def dynamic_membership_asof(universe, panel: MarketPanel) -> pd.Series:
    """Evaluate dynamic membership from the rows published by each decision time."""
    frame = panel.frame
    result = pd.Series(False, index=frame.index, name="eligible", dtype=bool)
    for symbol, group in frame.groupby(level="symbol", sort=False):
        if symbol not in universe.symbols:
            continue
        quote = group.close * group.volume
        available = group.available_time
        values = []
        for position, decision_time in enumerate(available):
            start = max(0, position - universe.lookback)
            history = quote.iloc[start:position]
            history_available = available.iloc[start:position]
            known = history.loc[history_available <= decision_time]
            coverage = known.notna().sum() / universe.lookback
            listing_age = int(known.notna().sum())
            values.append(bool(
                available.iloc[position] <= decision_time
                and listing_age >= universe.min_listing_bars
                and coverage >= universe.min_coverage
                and pd.notna(known.mean())
                and known.mean() >= universe.min_quote_volume
                and pd.notna(quote.iloc[position])
            ))
        result.loc[group.index] = values
    return result
