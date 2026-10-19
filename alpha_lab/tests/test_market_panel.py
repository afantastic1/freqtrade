import numpy as np
import pandas as pd
import pytest

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.universe import FixedUniverse, HistoricalUniverse
from alpha_lab.data.universe import DynamicUniverse
from alpha_lab.evaluation.cross_sectional import (
    cs_demean, cs_rank, cs_zscore, relative_momentum,
    relative_volatility, relative_volume,
)


def frame():
    times = pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True)
    index = pd.MultiIndex.from_tuples([
        (times[0], "BTC/USDT:USDT"), (times[0], "ETH/USDT:USDT"),
        (times[1], "BTC/USDT:USDT"),
    ], names=["open_time", "symbol"])
    return pd.DataFrame({
        "close": [10.0, 20.0, 11.0], "volume": [100.0, 50.0, 120.0],
        "available_time": [times[0], times[0], times[1]],
    }, index=index)


def test_market_panel_preserves_missing_records_and_groups_rolling_by_symbol():
    panel = MarketPanel(frame())
    assert len(panel.frame) == 3
    rolled = panel.rolling("close", window=2).mean()
    assert np.isnan(rolled.loc[(pd.Timestamp("2024-01-01", tz="UTC"), "BTC/USDT:USDT")])
    assert rolled.loc[(pd.Timestamp("2024-01-02", tz="UTC"), "BTC/USDT:USDT")] == 10.5
    assert np.isnan(rolled.loc[(pd.Timestamp("2024-01-01", tz="UTC"), "ETH/USDT:USDT")])


def test_market_panel_rejects_duplicates_and_non_utc():
    duplicated = pd.concat([frame(), frame().iloc[[0]]])
    with pytest.raises(ValueError, match="unique"):
        MarketPanel(duplicated)
    local = frame().reset_index()
    local["open_time"] = local["open_time"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        MarketPanel(local.set_index(["open_time", "symbol"]))


def test_fixed_and_historical_universe_never_invent_membership():
    panel = MarketPanel(frame())
    fixed = FixedUniverse("fixed-v1", ("BTC/USDT:USDT", "ETH/USDT:USDT"))
    membership = fixed.membership(panel)
    assert len(membership) == 3
    assert (pd.Timestamp("2024-01-02", tz="UTC"), "ETH/USDT:USDT") not in membership.index
    historical = HistoricalUniverse(
        "history-v1", pd.DataFrame({
            "symbol": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            "eligible_from": pd.to_datetime(["2023-01-01", "2024-01-02"], utc=True),
        })
    )
    members = historical.membership(panel)
    assert not members.loc[(pd.Timestamp("2024-01-01", tz="UTC"), "ETH/USDT:USDT")]


def test_historical_universe_validates_listing_history():
    with pytest.raises(ValueError, match="duplicate"):
        HistoricalUniverse("bad", pd.DataFrame({"symbol": ["A", "A"],
                                                  "eligible_from": pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True)}))
    with pytest.raises(ValueError, match="timezone"):
        HistoricalUniverse("bad", pd.DataFrame({"symbol": ["A"],
                                                  "eligible_from": pd.to_datetime(["2024-01-01"])}))


def test_cross_sectional_transforms_only_compare_same_timestamp():
    values = frame()["close"]
    ranks = cs_rank(values)
    assert ranks.loc[(pd.Timestamp("2024-01-01", tz="UTC"), "BTC/USDT:USDT")] == 0.5
    assert ranks.loc[(pd.Timestamp("2024-01-01", tz="UTC"), "ETH/USDT:USDT")] == 1.0
    assert ranks.loc[(pd.Timestamp("2024-01-02", tz="UTC"), "BTC/USDT:USDT")] == 1.0
    assert cs_demean(values).groupby(level="open_time").mean().abs().max() == pytest.approx(0)
    z = cs_zscore(values)
    assert np.isnan(z.loc[(pd.Timestamp("2024-01-02", tz="UTC"), "BTC/USDT:USDT")])


def test_dynamic_universe_uses_only_prior_available_candles():
    times = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
    index = pd.MultiIndex.from_product([times, ["A", "B"]], names=["open_time", "symbol"])
    prices = [10.0] * 8
    volumes = [1.0, 1.0, 1.0, 1.0, 1.0, 1000.0, 1.0, 1.0]
    data = pd.DataFrame({"close": prices, "volume": volumes,
                         "available_time": [t + pd.Timedelta(hours=1, seconds=1) for t, _ in index]}, index=index)
    panel = MarketPanel(data)
    universe = DynamicUniverse("liquid", ("A", "B"), lookback=2,
                               min_listing_bars=2, min_coverage=1.0, min_quote_volume=100.0)
    members = universe.membership(panel)
    assert not members.loc[(times[2], "B")]
    assert members.loc[(times[3], "B")]
    assert not members.loc[(times[3], "A")]


def test_panel_rejects_candle_available_before_close():
    data = frame()
    data["close_time"] = data.index.get_level_values("open_time") + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="available_time"):
        MarketPanel(data)


def test_relative_features_use_only_past_and_same_time_sections():
    times = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    index = pd.MultiIndex.from_product([times, ["A", "B"]], names=["open_time", "symbol"])
    data = pd.DataFrame({"close": [10, 20, 11, 20, 12, 20, 13, 20, 14, 20],
                         "volume": [1, 2, 2, 2, 3, 2, 4, 2, 5, 2],
                         "available_time": [t + pd.Timedelta(hours=1) for t, _ in index]}, index=index)
    panel = MarketPanel(data)
    for transform in (relative_momentum, relative_volatility, relative_volume):
        first = transform(panel, periods=2, timeframe="1h")
        altered = data.copy()
        altered.loc[(times[4], "B"), "close"] = 999
        altered.loc[(times[4], "B"), "volume"] = 999
        second = transform(MarketPanel(altered), periods=2, timeframe="1h")
        pd.testing.assert_series_equal(first.loc[(times[3], slice(None))],
                                       second.loc[(times[3], slice(None))])
    momentum = relative_momentum(panel, periods=2, timeframe="1h")
    assert momentum.loc[(times[2], "A")] > momentum.loc[(times[2], "B")]
    assert relative_volatility(panel, periods=2, timeframe="1h").notna().any()
    assert relative_volume(panel, periods=2, timeframe="1h").notna().any()
