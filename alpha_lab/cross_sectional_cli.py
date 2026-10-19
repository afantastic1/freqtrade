"""CLI for the trusted fixed-transform cross-sectional runner."""

import argparse
import json
from pathlib import Path

import pandas as pd

from alpha_lab.cross_sectional_runner import _validate_research_window, run_cross_sectional
from alpha_lab.data.market_panel import MarketPanel

RESEARCH_ROOT = Path("user_data/research/alpha_lab")


def _inside(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def load_bounded_panel_csv(path: str | Path) -> MarketPanel:
    frame = pd.read_csv(path)
    required = {"open_time", "symbol", "open", "high", "low", "close", "volume",
                "close_time", "available_time"}
    if required - set(frame):
        raise ValueError(f"panel CSV missing {sorted(required - set(frame))}")
    for key in ("open_time", "close_time", "available_time"):
        frame[key] = pd.to_datetime(frame[key], utc=True, errors="raise")
    if (frame.open_time >= pd.Timestamp("2025-01-01", tz="UTC")).any():
        raise ValueError("held-out data is forbidden")
    return MarketPanel(frame.set_index(["open_time", "symbol"]).sort_index())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--panel-csv", type=Path, required=True,
                        help="trusted 2022-2024 bounded OHLCV snapshot")
    args = parser.parse_args(argv)
    if not _inside(args.config, RESEARCH_ROOT / "candidates"):
        parser.error("config must be within the candidates directory")
    if not _inside(args.panel_csv, RESEARCH_ROOT / "panels"):
        parser.error("panel must be within the trusted panels directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    _validate_research_window(config)
    summary = run_cross_sectional(config, load_bounded_panel_csv(args.panel_csv), root=RESEARCH_ROOT)
    print(json.dumps({"run_id": summary["run_id"], "mode": summary["mode"],
                      "config_hash": summary["config_hash"],
                      "results": {key: value["status"] for key, value in summary["results"].items()}},
                     sort_keys=True))


if __name__ == "__main__":
    main()
