"""CLI routing tests for the v3 catalog commands."""

import json
from pathlib import Path

import pandas as pd
import pytest

from data_engineering.__main__ import main


def _source(root: Path):
    root.mkdir(parents=True)
    path = root / "BTC_USDT_USDT-5m-futures.feather"
    pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"], utc=True),
        "open": [100.0, 100.0], "high": [101.0, 101.0], "low": [99.0, 99.0],
        "close": [100.5, 100.5], "volume": [2.0, 2.0],
    }).to_feather(path)
    return path


def test_catalog_v3_discover_and_register_emit_json(tmp_path, capsys):
    source_root = tmp_path / "source"
    source = _source(source_root)
    catalog_root = tmp_path / "catalog"
    assert main([
        "--root", str(catalog_root), "catalog-v3", "discover",
        "--source-root-id", "binance-futures", "--source-root", str(source_root),
        "--exchange", "binance", "--market-type", "futures",
    ]) == 0
    discovered = json.loads(capsys.readouterr().out)
    assert discovered[0]["status"] == "AVAILABLE"
    assert discovered[0]["source_path"] == source.name

    assert main([
        "--root", str(catalog_root), "catalog-v3", "register",
        "--source-root-id", "binance-futures", "--source-root", str(source_root),
        "--relative-path", source.name, "--exchange", "binance", "--market-type", "futures",
        "--asset", "BTC", "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
        "--timeframe", "5m", "--source", "freqtrade-local",
    ]) == 0
    registered = json.loads(capsys.readouterr().out)
    assert registered["manifest"]["schema_version"] == "data-catalog-v3"


def test_catalog_v3_list_validate_and_coverage_emit_json(tmp_path, capsys):
    source_root = tmp_path / "source"
    source = _source(source_root)
    catalog_root = tmp_path / "catalog"
    common = [
        "--root", str(catalog_root), "catalog-v3", "register",
        "--source-root-id", "binance-futures", "--source-root", str(source_root),
        "--relative-path", source.name, "--exchange", "binance", "--market-type", "futures",
        "--asset", "BTC", "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
        "--timeframe", "5m", "--source", "freqtrade-local",
    ]
    main(common)
    dataset_id = json.loads(capsys.readouterr().out)["manifest"]["dataset_id"]
    roots = ["--source-root", f"binance-futures={source_root}"]

    assert main(["--root", str(catalog_root), "catalog-v3", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["manifest"]["dataset_id"] == dataset_id

    assert main(["--root", str(catalog_root), "catalog-v3", "validate",
                 "--dataset-id", dataset_id, *roots]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["status"] == "AVAILABLE"

    assert main([
        "--root", str(catalog_root), "catalog-v3", "coverage", "--dataset-id", dataset_id,
        "--start", "2025-01-01T00:00:00Z", "--end", "2025-01-01T00:10:00Z", *roots,
    ]) == 0
    covered = json.loads(capsys.readouterr().out)
    assert covered["status"] == "AVAILABLE"


def test_catalog_v3_register_accepts_explicit_event_cadence(tmp_path, capsys):
    source_root = tmp_path / "events"
    source_root.mkdir()
    source = source_root / "BTC_USDT_USDT-1h-funding_rate.feather"
    pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z"], utc=True),
        "funding_rate": [0.001, 0.002],
    }).to_feather(source)
    assert main([
        "--root", str(tmp_path / "catalog"), "catalog-v3", "register",
        "--source-root-id", "events", "--source-root", str(source_root),
        "--relative-path", source.name, "--exchange", "binance", "--market-type", "futures",
        "--asset", "BTC", "--exchange-symbol", "BTC/USDT:USDT",
        "--data-kind", "funding_rate", "--event-cadence", "1h",
        "--source", "freqtrade-local",
    ]) == 0
    registered = json.loads(capsys.readouterr().out)
    assert registered["manifest"]["event_cadence"] == "1h"


def test_legacy_inventory_cli_remains_available(tmp_path, capsys):
    assert main(["inventory", "--datadir", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"files": [], "contents_read": False}


def test_catalog_v3_rejects_invalid_market_type(tmp_path):
    with pytest.raises(SystemExit) as error:
        main([
            "catalog-v3", "discover", "--source-root-id", "local",
            "--source-root", str(tmp_path), "--exchange", "binance",
            "--market-type", "margin",
        ])
    assert error.value.code == 2
