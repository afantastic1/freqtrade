"""Maintainer-only adapter from existing Freqtrade Futures Feather to bounded panel snapshots."""

import argparse
import json
from pathlib import Path

import pandas as pd

from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.market_panel import MarketPanel


def prepare_panel(*, symbols: tuple[str, ...], timeframe: str, datadir: Path,
                  start: str, end: str, output: Path) -> dict:
    if timeframe not in ("5m", "15m", "1h", "4h") or len(set(symbols)) != len(symbols):
        raise ValueError("unsupported timeframe or duplicate symbol")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    start_ts = start_ts.tz_localize("UTC") if start_ts.tzinfo is None else start_ts.tz_convert("UTC")
    end_ts = end_ts.tz_localize("UTC") if end_ts.tzinfo is None else end_ts.tz_convert("UTC")
    if start_ts != start_ts.normalize() or end_ts != end_ts.normalize():
        raise ValueError("date bounds must be UTC midnight")
    if not (pd.Timestamp("2022-01-01", tz="UTC") <= start_ts < end_ts <= pd.Timestamp("2025-01-01", tz="UTC")):
        raise ValueError("held-out data is forbidden")
    assets = [load_ohlcv(symbol, timeframe, datadir, start_ts.strftime("%Y-%m-%d"),
                         end_ts.strftime("%Y-%m-%d")) for symbol in symbols]
    panel = MarketPanel.from_assets(assets)
    output.parent.mkdir(parents=True, exist_ok=True)
    panel.frame.reset_index().to_csv(output, index=False)
    report = {"timeframe": timeframe, "start": start, "end": end,
              "symbols": sorted(symbols), "data_fingerprint": panel.fingerprint(),
              "quality": panel.quality_report(timeframe),
              "source": "Freqtrade Futures Feather; no missing-candle fill",
              "survivorship_warning": "fixed symbol list may omit delisted contracts"}
    output.with_suffix(output.suffix + ".quality.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--timeframe", choices=("5m", "15m", "1h", "4h"), required=True)
    parser.add_argument("--datadir", type=Path, default=Path("user_data/data/binance"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = prepare_panel(symbols=tuple(args.symbol), timeframe=args.timeframe,
                           datadir=args.datadir, start=args.start, end=args.end, output=args.output)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
