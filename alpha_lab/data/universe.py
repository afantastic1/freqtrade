"""Point-in-time universe definitions."""

from dataclasses import dataclass
import hashlib
import json

import pandas as pd

from alpha_lab.data.market_panel import MarketPanel


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class FixedUniverse:
    universe_id: str
    symbols: tuple[str, ...]

    def __post_init__(self):
        if not self.universe_id or not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("fixed universe needs unique symbols and an ID")

    @property
    def universe_hash(self):
        return _hash({"type": "fixed", "id": self.universe_id, "symbols": sorted(self.symbols)})

    def membership(self, panel: MarketPanel) -> pd.Series:
        symbols = panel.frame.index.get_level_values("symbol")
        return pd.Series(symbols.isin(self.symbols), index=panel.frame.index, name="eligible")


@dataclass(frozen=True)
class HistoricalUniverse:
    universe_id: str
    listings: pd.DataFrame

    def __post_init__(self):
        if not self.universe_id or set(self.listings.columns) != {"symbol", "eligible_from"}:
            raise ValueError("historical universe requires symbol and eligible_from")
        if self.listings.symbol.isna().any() or self.listings.symbol.duplicated().any():
            raise ValueError("historical universe has duplicate or missing symbols")
        if not isinstance(self.listings.eligible_from.dtype, pd.DatetimeTZDtype):
            raise ValueError("eligible_from must be timezone-aware")
        listings = self.listings.copy(deep=True)
        listings["eligible_from"] = listings.eligible_from.dt.tz_convert("UTC")
        if listings.eligible_from.isna().any():
            raise ValueError("eligible_from contains NaT")
        object.__setattr__(self, "listings", listings.sort_values("symbol").reset_index(drop=True))

    @property
    def universe_hash(self):
        rows = self.listings.assign(eligible_from=lambda x: x.eligible_from.astype(str)).to_dict("records")
        return _hash({"type": "historical", "id": self.universe_id, "listings": rows})

    def membership(self, panel: MarketPanel) -> pd.Series:
        starts = self.listings.set_index("symbol")["eligible_from"]
        times = panel.frame.index.get_level_values("open_time")
        symbols = panel.frame.index.get_level_values("symbol")
        eligible = [symbol in starts.index and time >= starts.loc[symbol] for time, symbol in zip(times, symbols)]
        return pd.Series(eligible, index=panel.frame.index, name="eligible", dtype=bool)


@dataclass(frozen=True)
class DynamicUniverse:
    universe_id: str
    symbols: tuple[str, ...]
    lookback: int
    min_listing_bars: int
    min_coverage: float
    min_quote_volume: float

    def __post_init__(self):
        if self.lookback < 1 or self.min_listing_bars < 1:
            raise ValueError("lookback and min_listing_bars must be positive")
        if not 0 <= self.min_coverage <= 1 or self.min_quote_volume < 0:
            raise ValueError("invalid dynamic universe threshold")

    @property
    def universe_hash(self):
        return _hash({"type": "dynamic", "id": self.universe_id,
                      "symbols": sorted(self.symbols), "lookback": self.lookback,
                      "min_listing_bars": self.min_listing_bars,
                      "min_coverage": self.min_coverage,
                      "min_quote_volume": self.min_quote_volume})

    def membership(self, panel: MarketPanel) -> pd.Series:
        frame = panel.frame
        times = frame.index.get_level_values("open_time").unique().sort_values()
        quote = (frame["close"] * frame["volume"]).unstack("symbol").reindex(times)
        available = frame["available_time"].unstack("symbol").reindex(times)
        result = pd.DataFrame(False, index=times, columns=quote.columns)
        for symbol in quote.columns:
            if symbol not in self.symbols:
                continue
            prior = quote[symbol].shift(1)
            count = prior.notna().rolling(self.lookback, min_periods=1).sum()
            coverage = count / self.lookback
            mean_turnover = prior.rolling(self.lookback, min_periods=1).mean()
            age = quote[symbol].notna().cumsum().shift(1, fill_value=0)
            prior_available = available[symbol].shift(1)
            result[symbol] = ((age >= self.min_listing_bars)
                              & (coverage >= self.min_coverage)
                              & (mean_turnover >= self.min_quote_volume)
                              & (prior_available <= available[symbol])
                              & quote[symbol].notna())
        stacked = result.stack().reindex(frame.index, fill_value=False)
        return stacked.rename("eligible").astype(bool)
