import json

import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.panel_cli import prepare_panel
from alpha_lab.cross_sectional_cli import load_bounded_panel_csv


def test_prepare_panel_preserves_missing_candles_and_writes_quality(tmp_path, monkeypatch):
    times = pd.to_datetime(["2024-01-01 00:00Z", "2024-01-01 02:00Z"])

    def fake_loader(symbol, timeframe, datadir, start, end):
        frame = pd.DataFrame({"open_time": times, "close_time": times + pd.Timedelta(hours=1),
                              "available_time": times + pd.Timedelta(hours=1, seconds=1),
                              "open": [10., 11.], "high": [10., 11.], "low": [10., 11.],
                              "close": [10., 11.], "volume": [1., 1.]})
        return MarketData(symbol, timeframe, frame)

    monkeypatch.setattr("alpha_lab.panel_cli.load_ohlcv", fake_loader)
    output = tmp_path / "panel.csv"
    report = prepare_panel(symbols=("A", "B"), timeframe="1h", datadir=tmp_path,
                           start="2024-01-01", end="2024-01-02", output=output)
    assert len(pd.read_csv(output)) == 4
    assert report["quality"]["A"]["missing_bars"] == 1
    assert json.loads((tmp_path / "panel.csv.quality.json").read_text())["data_fingerprint"] == report["data_fingerprint"]
    assert load_bounded_panel_csv(output).fingerprint() == report["data_fingerprint"]
