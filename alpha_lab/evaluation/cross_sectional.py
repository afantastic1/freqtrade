"""Fixed point-in-time cross-sectional transforms."""

import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.timeframe import timeframe_to_seconds


def _validate(values: pd.Series) -> None:
    if not isinstance(values.index, pd.MultiIndex) or "open_time" not in values.index.names:
        raise ValueError("cross-sectional values require a MarketPanel index")


def cs_rank(values: pd.Series) -> pd.Series:
    _validate(values)
    return values.groupby(level="open_time").rank(method="average", pct=True)


def cs_demean(values: pd.Series) -> pd.Series:
    _validate(values)
    return values - values.groupby(level="open_time").transform("mean")


def cs_zscore(values: pd.Series) -> pd.Series:
    _validate(values)
    mean = values.groupby(level="open_time").transform("mean")
    std = values.groupby(level="open_time").transform("std")
    return ((values - mean) / std.replace(0.0, np.nan)).astype(float)


def _per_asset_series(panel: MarketPanel, periods: int, timeframe: str, kind: str) -> pd.Series:
    if type(periods) is not int or periods < 1:
        raise ValueError("periods must be a positive integer")
    step = pd.Timedelta(seconds=timeframe_to_seconds(timeframe))
    result = pd.Series(np.nan, index=panel.frame.index, dtype=float)
    for _, group in panel.frame.groupby(level="symbol", sort=False):
        times = pd.Series(group.index.get_level_values("open_time"), index=group.index)
        close = group["close"]
        if kind == "momentum":
            value = close / close.shift(periods) - 1
            valid = times.sub(times.shift(periods)).eq(periods * step)
        elif kind == "volatility":
            ret = np.log(close / close.shift(1))
            ret = ret.where(times.sub(times.shift(1)).eq(step))
            value = ret.rolling(periods, min_periods=periods).std(ddof=0)
            valid = times.sub(times.shift(periods)).eq(periods * step)
        else:
            trailing = group["volume"].shift(1).rolling(periods, min_periods=periods).mean()
            value = group["volume"] / trailing.replace(0, np.nan)
            valid = times.sub(times.shift(periods)).eq(periods * step)
        result.loc[group.index] = value.where(valid).to_numpy()
    return result


def relative_momentum(panel: MarketPanel, *, periods: int, timeframe: str) -> pd.Series:
    return cs_demean(_per_asset_series(panel, periods, timeframe, "momentum"))


def relative_volatility(panel: MarketPanel, *, periods: int, timeframe: str) -> pd.Series:
    return cs_demean(_per_asset_series(panel, periods, timeframe, "volatility"))


def relative_volume(panel: MarketPanel, *, periods: int, timeframe: str) -> pd.Series:
    return cs_demean(_per_asset_series(panel, periods, timeframe, "volume"))
