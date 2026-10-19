import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.labels.cross_sectional import cross_sectional_labels


def _panel(missing=False):
    times = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    rows = []
    for i, time in enumerate(times):
        for symbol, base in (("A", 10.0), ("B", 20.0)):
            if missing and symbol == "B" and i == 2:
                continue
            price = base + i * (2 if symbol == "A" else 1)
            rows.append((time, symbol, price, time + pd.Timedelta(hours=1),
                         time + pd.Timedelta(hours=1, seconds=1)))
    frame = pd.DataFrame(rows, columns=["open_time", "symbol", "open", "close_time", "available_time"])
    frame["close"] = frame["open"]
    return MarketPanel(frame.set_index(["open_time", "symbol"]))


def test_labels_are_per_asset_and_relative_to_same_time_universe():
    panel = _panel()
    member = pd.Series(True, index=panel.frame.index)
    labels = cross_sectional_labels(panel, member, timeframe="1h", horizon=2)
    time = pd.Timestamp("2024-01-01", tz="UTC")
    a = labels.loc[(time, "A")]
    b = labels.loc[(time, "B")]
    assert a.raw_return == (14 / 12 - 1)
    assert b.raw_return == (22 / 21 - 1)
    assert a.relative_return == (a.raw_return - b.raw_return) / 2
    assert not a.entry_time_feasible
    assert a.label_available_time > a.decision_time


def test_missing_future_candle_invalidates_only_affected_asset():
    panel = _panel(missing=True)
    labels = cross_sectional_labels(panel, pd.Series(True, index=panel.frame.index),
                                    timeframe="1h", horizon=2)
    time = pd.Timestamp("2024-01-01", tz="UTC")
    assert np.isnan(labels.loc[(time, "B"), "raw_return"])
    assert np.isfinite(labels.loc[(time, "A"), "raw_return"])
