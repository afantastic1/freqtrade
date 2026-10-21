"""Point-in-time multi-asset market panel."""

from dataclasses import dataclass
import hashlib
import json

import numpy as np
import pandas as pd

from alpha_lab.data.timeframe import timeframe_to_seconds


@dataclass(frozen=True)
class _GroupedRolling:
    values: pd.Series
    index: pd.MultiIndex
    window: int
    min_periods: int

    def mean(self) -> pd.Series:
        result = self.values.groupby(level="symbol", group_keys=False).rolling(
            self.window, min_periods=self.min_periods
        ).mean()
        return result.droplevel(0).reindex(self.index)


@dataclass(frozen=True)
class MarketPanel:
    frame: pd.DataFrame

    def __post_init__(self):
        frame = self.frame.copy(deep=True)
        if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ["open_time", "symbol"]:
            raise ValueError("MarketPanel index must be (open_time, symbol)")
        if not frame.index.is_unique:
            raise ValueError("MarketPanel index must be unique")
        times = frame.index.get_level_values("open_time")
        if times.tz is None or str(times.tz) != "UTC":
            raise ValueError("open_time must be UTC")
        if "available_time" not in frame:
            raise ValueError("available_time is required")
        if not isinstance(frame.available_time.dtype, pd.DatetimeTZDtype):
            raise ValueError("available_time must be timezone-aware")
        frame["available_time"] = frame.available_time.dt.tz_convert("UTC")
        if frame.available_time.isna().any():
            raise ValueError("available_time contains NaT")
        if "close_time" in frame:
            if not isinstance(frame.close_time.dtype, pd.DatetimeTZDtype):
                raise ValueError("close_time must be timezone-aware")
            frame["close_time"] = frame.close_time.dt.tz_convert("UTC")
            if (frame.available_time < frame.close_time).any():
                raise ValueError("available_time precedes close_time")
        if not frame.index.is_monotonic_increasing:
            frame = frame.sort_index()
        object.__setattr__(self, "frame", frame)

    @classmethod
    def from_assets(cls, assets):
        """Combine validated MarketData without creating missing candles."""
        frames = []
        for asset in assets:
            part = asset.frame.copy(deep=True)
            part["symbol"] = asset.symbol
            frames.append(part.set_index(["open_time", "symbol"]))
        if not frames:
            raise ValueError("at least one asset is required")
        return cls(pd.concat(frames).sort_index())

    def quality_report(self, timeframe: str) -> dict:
        step = pd.Timedelta(seconds=timeframe_to_seconds(timeframe))
        result = {}
        for symbol, group in self.frame.groupby(level="symbol"):
            times = group.index.get_level_values("open_time")
            expected = int((times[-1] - times[0]) / step) + 1
            result[symbol] = {
                "first_open_time": times[0].isoformat(),
                "last_open_time": times[-1].isoformat(),
                "observed_bars": len(times), "expected_bars_between_first_last": expected,
                "missing_bars": expected - len(times),
                "coverage": len(times) / expected,
            }
        return result

    def fingerprint(self) -> str:
        canonical = self.frame.sort_index().sort_index(axis=1)
        row_hash = pd.util.hash_pandas_object(canonical, index=True).to_numpy(np.uint64)
        descriptor = json.dumps({"columns": list(canonical), "dtypes": [str(t) for t in canonical.dtypes]}, sort_keys=True)
        digest = hashlib.sha256(descriptor.encode())
        digest.update(row_hash.tobytes())
        return digest.hexdigest()

    def rolling(self, column: str, *, window: int, min_periods: int | None = None):
        if window < 1:
            raise ValueError("window must be positive")
        return _GroupedRolling(
            self.frame[column], self.frame.index, window, min_periods or window
        )
