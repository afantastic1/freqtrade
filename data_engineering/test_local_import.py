import json
from pathlib import Path

import pandas as pd

from data_engineering.__main__ import main
from data_engineering.freqtrade_feather import register_feather_file
from data_engineering.local_import import import_freqtrade_directory


ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")


def _ohlcv(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": pd.to_datetime(
            ["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"], utc=True
        ),
        "open": [100.0, 100.0], "high": [101.0, 101.0],
        "low": [99.0, 99.0], "close": [100.5, 100.5], "volume": [2.0, 2.0],
    }).to_feather(path)


def _funding(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": pd.to_datetime(
            ["2025-01-01T00:00:00Z", "2025-01-01T08:00:00Z"], utc=True
        ),
        "funding_rate": [0.0001, 0.0002],
    }).to_feather(path)


def test_import_local_registers_five_assets_and_is_idempotent(tmp_path):
    datadir = tmp_path / "binance"
    for asset in ASSETS:
        _ohlcv(datadir / f"{asset}_USDT-5m.feather")

    first = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=ASSETS, market_types=("spot",), timeframes=("5m",),
        data_kinds=("ohlcv",),
    )
    second = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=ASSETS, market_types=("spot",), timeframes=("5m",),
        data_kinds=("ohlcv",),
    )

    assert first["summary"] == {"FAILED": 0, "REGISTERED": 5, "SKIPPED_DUPLICATE": 0, "SKIPPED_PRESENT": 0, "total": 5}
    assert second["summary"] == {"FAILED": 0, "REGISTERED": 0, "SKIPPED_DUPLICATE": 0, "SKIPPED_PRESENT": 5, "total": 5}
    assert [row["asset"] for row in first["datasets"]] == sorted(ASSETS)
    assert all(row["status"] == "SKIPPED_PRESENT" for row in second["datasets"])
    assert all(row["network_access"] is False for row in second["datasets"])


def test_import_local_does_not_reregister_valid_partial_dataset(tmp_path):
    datadir = tmp_path / "binance"
    path = datadir / "BTC_USDT-5m.feather"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": pd.to_datetime(
            ["2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z"], utc=True
        ),
        "open": [100.0, 100.0], "high": [101.0, 101.0],
        "low": [99.0, 99.0], "close": [100.5, 100.5], "volume": [2.0, 2.0],
    }).to_feather(path)

    first = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        market_types=("spot",), timeframes=("5m",), data_kinds=("ohlcv",),
    )
    second = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        market_types=("spot",), timeframes=("5m",), data_kinds=("ohlcv",),
    )

    assert first["datasets"][0]["quality_status"] == "PARTIAL"
    assert second["summary"]["SKIPPED_PRESENT"] == 1


def test_import_local_discovers_spot_and_futures_without_cross_classifying(tmp_path):
    datadir = tmp_path / "binance"
    _ohlcv(datadir / "BTC_USDT-5m.feather")
    _ohlcv(datadir / "futures" / "BTC_USDT_USDT-5m-futures.feather")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=("BTC",), market_types=("spot", "futures"),
        timeframes=("5m",), data_kinds=("ohlcv",),
    )

    assert [(row["market_type"], row["exchange_symbol"]) for row in result["datasets"]] == [
        ("futures", "BTC/USDT:USDT"), ("spot", "BTC/USDT")
    ]


def test_import_local_isolates_invalid_file_and_imports_other_datasets(tmp_path):
    datadir = tmp_path / "binance"
    _ohlcv(datadir / "BTC_USDT-5m.feather")
    bad = datadir / "ETH_USDT-5m.feather"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a feather file")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=("BTC", "ETH"), market_types=("spot",),
        timeframes=("5m",), data_kinds=("ohlcv",),
    )

    by_asset = {row["asset"]: row for row in result["datasets"]}
    assert by_asset["BTC"]["status"] == "REGISTERED"
    assert by_asset["ETH"]["status"] == "FAILED"
    assert by_asset["ETH"]["error_code"] == "SOURCE_INVALID"
    assert result["summary"]["FAILED"] == 1


def test_import_local_isolates_malformed_parsed_identity(tmp_path):
    datadir = tmp_path / "binance"
    _ohlcv(datadir / "BTC_USDT-5m.feather")
    _ohlcv(datadir / "_USDT-5m.feather")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        market_types=("spot",), timeframes=("5m",), data_kinds=("ohlcv",),
    )

    assert result["summary"] == {
        "FAILED": 1, "REGISTERED": 1, "SKIPPED_DUPLICATE": 0,
        "SKIPPED_PRESENT": 0, "total": 2,
    }
    assert next(row for row in result["datasets"] if row["asset"] == "BTC")["status"] == "REGISTERED"
    malformed = next(row for row in result["datasets"] if row["asset"] == "")
    assert malformed["status"] == "FAILED"
    assert malformed["error_code"] == "SOURCE_INVALID"


def test_import_local_reuses_event_identity_with_explicit_cadence(tmp_path):
    datadir = tmp_path / "binance"
    funding = datadir / "futures" / "BTC_USDT_USDT-1h-funding_rate.feather"
    _funding(funding)
    catalog = tmp_path / "catalog"
    register_feather_file(
        catalog, source_root_id="freqtrade-local-futures",
        source_root=datadir / "futures", relative_path=Path(funding.name),
        exchange="binance", market_type="futures", asset="BTC",
        exchange_symbol="BTC/USDT:USDT", data_kind="funding_rate",
        timeframe=None, event_cadence="1h", source="freqtrade-local",
    )

    result = import_freqtrade_directory(
        catalog, datadir, source_root_id="freqtrade-local",
        assets=("BTC",), market_types=("futures",), timeframes=("1h",),
        data_kinds=("funding_rate",),
    )

    assert result["summary"] == {
        "FAILED": 0, "REGISTERED": 0, "SKIPPED_DUPLICATE": 0,
        "SKIPPED_PRESENT": 1, "total": 1,
    }
    assert result["datasets"][0]["timeframe"] is None
    assert result["datasets"][0]["event_cadence"] is None


def test_import_local_does_not_infer_event_cadence_from_storage_label(tmp_path):
    datadir = tmp_path / "binance"
    funding = datadir / "futures" / "ETH_USDT_USDT-1h-funding_rate.feather"
    _funding(funding)

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=("ETH",), market_types=("futures",), timeframes=("1h",),
        data_kinds=("funding_rate",),
    )

    assert result["datasets"][0]["status"] == "REGISTERED"
    assert result["datasets"][0]["quality_status"] == "AVAILABLE"
    assert result["datasets"][0]["timeframe"] is None
    assert result["datasets"][0]["event_cadence"] is None
    assert result["datasets"][0]["storage_timeframe"] == "1h"


def test_import_local_prefers_finer_storage_for_duplicate_event_identity(tmp_path):
    datadir = tmp_path / "binance"
    _funding(datadir / "futures" / "BTC_USDT_USDT-1h-funding_rate.feather")
    _funding(datadir / "futures" / "BTC_USDT_USDT-5m-funding_rate.feather")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=("BTC",), market_types=("futures",),
        data_kinds=("funding_rate",),
    )

    by_storage = {row["storage_timeframe"]: row for row in result["datasets"]}
    assert by_storage["5m"]["status"] == "REGISTERED"
    assert by_storage["1h"]["status"] == "SKIPPED_DUPLICATE"
    assert result["summary"] == {
        "FAILED": 0, "REGISTERED": 1, "SKIPPED_DUPLICATE": 1,
        "SKIPPED_PRESENT": 0, "total": 2,
    }


def test_import_local_sorting_does_not_abort_on_uppercase_storage_unit(tmp_path):
    datadir = tmp_path / "binance"
    _funding(datadir / "futures" / "BTC_USDT_USDT-1H-funding_rate.feather")
    _ohlcv(datadir / "ETH_USDT-5m.feather")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
    )

    assert result["summary"]["total"] == 2
    assert any(row["asset"] == "ETH" and row["status"] == "REGISTERED"
               for row in result["datasets"])


def test_import_local_falls_back_when_preferred_event_file_is_invalid(tmp_path):
    datadir = tmp_path / "binance"
    bad = datadir / "futures" / "BTC_USDT_USDT-5m-funding_rate.feather"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a feather file")
    _funding(datadir / "futures" / "BTC_USDT_USDT-1h-funding_rate.feather")

    result = import_freqtrade_directory(
        tmp_path / "catalog", datadir, source_root_id="freqtrade-local",
        assets=("BTC",), market_types=("futures",), data_kinds=("funding_rate",),
    )

    by_storage = {row["storage_timeframe"]: row for row in result["datasets"]}
    assert by_storage["5m"]["status"] == "FAILED"
    assert by_storage["1h"]["status"] == "REGISTERED"


def test_import_local_cli_supports_repeatable_filtered_import(tmp_path, capsys):
    datadir = tmp_path / "binance"
    _ohlcv(datadir / "BTC_USDT-5m.feather")

    arguments = [
        "--root", str(tmp_path / "catalog"), "catalog-v3", "import-local",
        "--datadir", str(datadir), "--source-root-id", "freqtrade-local",
        "--asset", "BTC", "--market-type", "spot", "--timeframe", "5m",
        "--data-kind", "ohlcv",
    ]
    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["summary"]["REGISTERED"] == 1
    assert second["summary"]["SKIPPED_PRESENT"] == 1
