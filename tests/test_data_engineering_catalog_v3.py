import importlib.util
import importlib
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pandas as pd
import pytest

from jsonschema import Draft202012Validator


def _v3_manifest(*, data_kind="ohlcv", market_type="futures", timeframe="5m"):
    if data_kind in {"funding_rate", "open_interest"}:
        timeframe = None
    return {
        "schema_version": "data-catalog-v3",
        "dataset_id": f"binance-{market_type}-btc-{data_kind}",
        "exchange": "binance",
        "market_type": market_type,
        "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT" if market_type == "futures" else "BTC/USDT",
        "data_kind": data_kind,
        "timeframe": timeframe,
        "source": "freqtrade-local",
        "source_reference": "user_data/data/binance",
        "registered_at": "2026-10-20T00:00:00Z",
        "quality": {"status": "AVAILABLE", "issues": []},
        "partitions": [
            {
                "start": "2025-01-01T00:00:00Z",
                "end": "2025-02-01T00:00:00Z",
                "source_root_id": "binance-data",
                "source_path": "futures/BTC_USDT_USDT-5m-futures.feather",
                "first_observation": "2025-01-01T00:00:00Z",
                "last_observation": "2025-01-31T23:55:00Z",
                "row_count": 8928,
                "source_sha256": "a" * 64,
                "fingerprint": "b" * 64,
                "quality_status": "AVAILABLE",
            }
        ],
        "revision": {"previous_manifest_sha256": None, "reason": "initial registration"},
    }


def _v3_catalog_module():
    module_spec = importlib.util.find_spec("data_engineering.catalog_v3")
    assert module_spec is not None, "v3 catalog module must exist"
    return importlib.import_module("data_engineering.catalog_v3")


def _freqtrade_feather_module():
    module_spec = importlib.util.find_spec("data_engineering.freqtrade_feather")
    assert module_spec is not None, "Freqtrade Feather adapter must exist"
    return importlib.import_module("data_engineering.freqtrade_feather")


def _quality_contracts_module():
    package_spec = importlib.util.find_spec("data_engineering.quality")
    assert package_spec is not None, "data-kind quality package must exist"
    module_spec = importlib.util.find_spec("data_engineering.quality.contracts")
    assert module_spec is not None, "data-kind quality contracts must exist"
    return importlib.import_module("data_engineering.quality.contracts")


def _ohlcv_frame(dates):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": [100.0] * len(dates),
            "high": [101.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [100.5] * len(dates),
            "volume": [2.0] * len(dates),
        }
    )


@pytest.mark.parametrize(
    ("filename", "market_type", "data_kind", "expected_symbol"),
    [
        ("BTC_USDT_USDT-5m-futures.feather", "futures", "ohlcv", "BTC/USDT:USDT"),
        ("BTC_USDT_USDT-1h-funding_rate.feather", "futures", "funding_rate", "BTC/USDT:USDT"),
        ("BTC_USDT_USDT-1h-open_interest.feather", "futures", "open_interest", "BTC/USDT:USDT"),
        ("BTC_USDT-5m.feather", "spot", "ohlcv", "BTC/USDT"),
    ],
)
def test_discover_feather_datasets_parses_supported_freqtrade_names(
    tmp_path, filename, market_type, data_kind, expected_symbol
):
    adapter = _freqtrade_feather_module()
    path = tmp_path / filename
    dates = ["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"]
    if data_kind == "ohlcv":
        frame = _ohlcv_frame(dates)
    elif data_kind == "funding_rate":
        frame = pd.DataFrame({"date": pd.to_datetime(dates, utc=True), "funding_rate": [0.001, -0.001]})
    else:
        frame = pd.DataFrame({"date": pd.to_datetime(dates, utc=True), "open_interest_amount": [1.0, 2.0]})
    frame.to_feather(path)

    found = adapter.discover_feather_datasets(
        "binance-local", tmp_path, exchange="binance", market_type=market_type
    )

    assert len(found) == 1
    assert found[0]["status"] == "AVAILABLE"
    assert found[0]["exchange"] == "binance"
    assert found[0]["market_type"] == market_type
    assert found[0]["asset"] == "BTC"
    assert found[0]["exchange_symbol"] == expected_symbol
    assert found[0]["data_kind"] == data_kind
    assert found[0]["source_path"] == filename


def test_discover_feather_surfaces_unsupported_filename_as_invalid(tmp_path):
    adapter = _freqtrade_feather_module()
    path = tmp_path / "unmapped-file.feather"
    _ohlcv_frame(["2025-01-01T00:00:00Z"]).to_feather(path)

    found = adapter.discover_feather_datasets(
        "binance-local", tmp_path, exchange="binance", market_type="futures"
    )

    assert found[0]["status"] == "INVALID"
    assert "filename" in " ".join(found[0]["issues"]).lower()


def test_register_feather_file_hashes_source_and_builds_monthly_partitions(tmp_path):
    adapter = _freqtrade_feather_module()
    source_root = tmp_path / "futures"
    source_root.mkdir()
    source = source_root / "BTC_USDT_USDT-5m-futures.feather"
    dates = [
        "2025-01-31T23:55:00Z",
        "2025-02-01T00:00:00Z",
        "2025-02-01T00:05:00Z",
    ]
    _ohlcv_frame(dates).to_feather(source)
    source_bytes = source.read_bytes()
    catalog_root = tmp_path / "catalog"

    result = adapter.register_feather_file(
        catalog_root,
        source_root_id="binance-futures",
        source_root=source_root,
        relative_path=Path(source.name),
        exchange="binance",
        market_type="futures",
        asset="BTC",
        exchange_symbol="BTC/USDT:USDT",
        data_kind="ohlcv",
        timeframe="5m",
        source="freqtrade-local",
    )

    manifest = result["manifest"]
    assert [item["row_count"] for item in manifest["partitions"]] == [1, 2]
    assert manifest["partitions"][0]["start"] == "2025-01-01T00:00:00Z"
    assert manifest["partitions"][0]["end"] == "2025-02-01T00:00:00Z"
    assert manifest["partitions"][1]["start"] == "2025-02-01T00:00:00Z"
    assert all(item["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
               for item in manifest["partitions"])
    assert source.read_bytes() == source_bytes


def test_register_feather_file_is_idempotent_and_links_changed_source_revision(tmp_path):
    adapter = _freqtrade_feather_module()
    source_root = tmp_path / "futures"
    source_root.mkdir()
    source = source_root / "BTC_USDT_USDT-5m-futures.feather"
    dates = ["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"]
    _ohlcv_frame(dates).to_feather(source)
    catalog_root = tmp_path / "catalog"
    arguments = {
        "source_root_id": "binance-futures",
        "source_root": source_root,
        "relative_path": Path(source.name),
        "exchange": "binance",
        "market_type": "futures",
        "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT",
        "data_kind": "ohlcv",
        "timeframe": "5m",
        "source": "freqtrade-local",
    }

    first = adapter.register_feather_file(catalog_root, **arguments)
    repeated = adapter.register_feather_file(catalog_root, **arguments)
    assert repeated["manifest_sha256"] == first["manifest_sha256"]

    revised = _ohlcv_frame(dates)
    revised.loc[1, "close"] = 100.75
    revised.to_feather(source)
    second = adapter.register_feather_file(catalog_root, **arguments)

    assert second["manifest_sha256"] != first["manifest_sha256"]
    assert second["manifest"]["revision"]["previous_manifest_sha256"] == first["manifest_sha256"]


def test_register_feather_file_rejects_source_root_escape_and_symlink(tmp_path):
    adapter = _freqtrade_feather_module()
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside.feather"
    _ohlcv_frame(["2025-01-01T00:00:00Z"]).to_feather(outside)
    (source_root / "link.feather").symlink_to(outside)
    identity = {
        "catalog_root": tmp_path / "catalog",
        "source_root_id": "local-source",
        "source_root": source_root,
        "exchange": "binance",
        "market_type": "futures",
        "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT",
        "data_kind": "ohlcv",
        "timeframe": "5m",
        "source": "freqtrade-local",
    }

    with pytest.raises(ValueError, match="source path escapes configured root"):
        adapter.register_feather_file(relative_path=Path("../outside.feather"), **identity)
    with pytest.raises(ValueError, match="symlink"):
        adapter.register_feather_file(relative_path=Path("link.feather"), **identity)


def test_validate_observations_reports_available_gapless_ohlcv():
    quality = _quality_contracts_module()

    result = quality.validate_observations(
        _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"]),
        data_kind="ohlcv",
        timeframe="5m",
    )

    assert result["status"] == "AVAILABLE"
    assert result["row_count"] == 2
    assert result["cadence"]["missing_observations"] == 0
    assert result["issues"] == []


def test_validate_observations_marks_missing_candle_as_partial():
    quality = _quality_contracts_module()

    result = quality.validate_observations(
        _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z"]),
        data_kind="ohlcv",
        timeframe="5m",
    )

    assert result["status"] == "PARTIAL"
    assert result["cadence"]["missing_observations"] == 1


@pytest.mark.parametrize(
    ("dates", "timeframe", "code"),
    [
        (["2025-01-01T00:00:00Z", "2025-01-01T00:06:00Z"], "5m", "TIMEFRAME_MISALIGNED"),
        (["2025-01-01T00:00:00", "2025-01-01T00:05:00"], "5m", "NAIVE_TIMESTAMP"),
    ],
)
def test_validate_observations_rejects_misaligned_or_naive_timestamps(dates, timeframe, code):
    quality = _quality_contracts_module()
    frame = _ohlcv_frame(dates)
    if code == "NAIVE_TIMESTAMP":
        frame["date"] = pd.to_datetime(dates)

    result = quality.validate_observations(
        frame, data_kind="ohlcv", timeframe=timeframe
    )

    assert result["status"] == "INVALID"
    assert code in {issue["code"] for issue in result["issues"]}


def test_validate_observations_rejects_duplicate_timestamps():
    quality = _quality_contracts_module()

    result = quality.validate_observations(
        _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"]),
        data_kind="ohlcv",
        timeframe="5m",
    )

    assert result["status"] == "INVALID"
    assert "DUPLICATE_TIMESTAMP" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize(
    ("column", "values", "code"),
    [
        ("close", [100.0, float("inf")], "NONFINITE_VALUE"),
        ("close", [100.0, 0.0], "NONPOSITIVE_PRICE"),
        ("volume", [2.0, -1.0], "NEGATIVE_VOLUME"),
        ("high", [101.0, 99.0], "INVALID_OHLC_BOUNDS"),
    ],
)
def test_validate_observations_rejects_invalid_ohlcv_values(column, values, code):
    quality = _quality_contracts_module()
    frame = _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"])
    frame[column] = values

    result = quality.validate_observations(frame, data_kind="ohlcv", timeframe="5m")

    assert result["status"] == "INVALID"
    assert code in {issue["code"] for issue in result["issues"]}


def test_validate_observations_keeps_negative_funding_rates_but_rejects_infinity():
    quality = _quality_contracts_module()
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z"], utc=True),
            "funding_rate": [-0.001, 0.001],
        }
    )

    valid = quality.validate_observations(frame, data_kind="funding_rate", timeframe="1h")
    frame.loc[1, "funding_rate"] = float("inf")
    invalid = quality.validate_observations(frame, data_kind="funding_rate", timeframe="1h")

    assert valid["status"] == "AVAILABLE"
    assert invalid["status"] == "INVALID"
    assert "NONFINITE_VALUE" in {issue["code"] for issue in invalid["issues"]}


def test_validate_observations_requires_nonnegative_open_interest_measure():
    quality = _quality_contracts_module()
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
            "open_interest_amount": [-1.0],
        }
    )

    result = quality.validate_observations(frame, data_kind="open_interest", timeframe="1h")

    assert result["status"] == "INVALID"
    assert "NEGATIVE_OPEN_INTEREST" in {issue["code"] for issue in result["issues"]}


def test_validate_observations_requires_positive_mark_price():
    quality = _quality_contracts_module()
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
            "mark_price": [0.0],
        }
    )

    result = quality.validate_observations(frame, data_kind="mark_price", timeframe="1h")

    assert result["status"] == "INVALID"
    assert "NONPOSITIVE_PRICE" in {issue["code"] for issue in result["issues"]}


def _registered_feather(tmp_path, frame=None):
    adapter = _freqtrade_feather_module()
    source_root = tmp_path / "futures"
    source_root.mkdir(exist_ok=True)
    source = source_root / "BTC_USDT_USDT-5m-futures.feather"
    if frame is None:
        frame = _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"])
    frame.to_feather(source)
    record = adapter.register_feather_file(
        tmp_path / "catalog",
        source_root_id="binance-futures",
        source_root=source_root,
        relative_path=Path(source.name),
        exchange="binance",
        market_type="futures",
        asset="BTC",
        exchange_symbol="BTC/USDT:USDT",
        data_kind="ohlcv",
        timeframe="5m",
        source="freqtrade-local",
    )
    return record, source_root, source


def test_validate_dataset_detects_source_hash_mismatch(tmp_path):
    module_spec = importlib.util.find_spec("data_engineering.catalog_v3")
    assert module_spec is not None, "v3 catalog module must exist"
    catalog = importlib.import_module("data_engineering.catalog_v3")
    assert hasattr(catalog, "validate_dataset"), "v3 dataset validator must exist"
    record, source_root, source = _registered_feather(tmp_path)
    changed = _ohlcv_frame(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"])
    changed.loc[1, "close"] = 101.0
    changed.to_feather(source)

    result = catalog.validate_dataset(
        tmp_path / "catalog",
        record["manifest"]["dataset_id"],
        source_roots={"binance-futures": source_root},
    )

    assert result["status"] == "INVALID"
    assert "SOURCE_HASH_MISMATCH" in {issue["code"] for issue in result["issues"]}


def test_validate_dataset_detects_partition_fingerprint_mismatch(tmp_path):
    catalog = importlib.import_module("data_engineering.catalog_v3")
    assert hasattr(catalog, "validate_dataset"), "v3 dataset validator must exist"
    record, source_root, _ = _registered_feather(tmp_path)
    manifest = deepcopy(record["manifest"])
    manifest["partitions"][0]["fingerprint"] = "f" * 64
    tampered = catalog.register_dataset(tmp_path / "tampered-catalog", manifest)

    result = catalog.validate_dataset(
        tmp_path / "tampered-catalog",
        tampered["manifest"]["dataset_id"],
        source_roots={"binance-futures": source_root},
    )

    assert result["status"] == "INVALID"
    assert "PARTITION_FINGERPRINT_MISMATCH" in {issue["code"] for issue in result["issues"]}


def test_data_kind_registry_declares_required_contract_fields():
    module_spec = importlib.util.find_spec("data_engineering.data_kind_registry")
    assert module_spec is not None, "v3 data-kind registry module must exist"
    from data_engineering.data_kind_registry import DATA_KIND_CONTRACTS

    assert set(DATA_KIND_CONTRACTS) == {"ohlcv", "funding_rate", "open_interest", "mark_price"}
    assert DATA_KIND_CONTRACTS["ohlcv"]["timestamp_field"] == "date"
    assert DATA_KIND_CONTRACTS["ohlcv"]["required_fields"] == {
        "date", "open", "high", "low", "close", "volume"
    }
    assert DATA_KIND_CONTRACTS["funding_rate"]["timestamp_field"] == "date"
    assert DATA_KIND_CONTRACTS["funding_rate"]["required_fields"] == {"date", "funding_rate"}
    assert DATA_KIND_CONTRACTS["open_interest"]["required_fields"] == {"date"}
    assert DATA_KIND_CONTRACTS["open_interest"]["one_of_fields"] == {
        "open_interest_amount", "open_interest_value"
    }
    assert DATA_KIND_CONTRACTS["mark_price"]["required_fields"] == {"date", "mark_price"}
    assert all("cadence_policy" in value and "validators" in value
               for value in DATA_KIND_CONTRACTS.values())


def test_v3_schema_accepts_ohlcv_funding_oi_and_spot_futures_identities():
    schema_path = files("data_engineering").joinpath(
        "schemas", "dataset-manifest-v3.schema.json"
    )
    assert schema_path.is_file(), "v3 manifest schema must be packaged"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    validator.validate(_v3_manifest())
    validator.validate(_v3_manifest(data_kind="funding_rate"))
    validator.validate(_v3_manifest(data_kind="open_interest"))
    validator.validate(_v3_manifest(data_kind="mark_price", market_type="spot"))


def _v3_validator():
    path = files("data_engineering").joinpath(
        "schemas", "dataset-manifest-v3.schema.json"
    )
    assert path.is_file(), "v3 manifest schema must be packaged"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def test_v3_schema_rejects_unknown_data_kind():
    validator = _v3_validator()
    unknown_kind = _v3_manifest()
    unknown_kind["data_kind"] = "unknown"
    assert list(validator.iter_errors(unknown_kind))


def test_v3_schema_rejects_missing_dataset_identity():
    validator = _v3_validator()
    missing_asset = _v3_manifest()
    del missing_asset["asset"]
    assert list(validator.iter_errors(missing_asset))


def test_v3_schema_rejects_incomplete_partition_integrity_record():
    validator = _v3_validator()
    bad_partition = _v3_manifest()
    del bad_partition["partitions"][0]["source_sha256"]
    assert list(validator.iter_errors(bad_partition))


def test_v3_schema_keeps_v1_v2_schema_resources_unchanged_and_available():
    schemas = files("data_engineering").joinpath("schemas")
    v1 = json.loads(schemas.joinpath("catalog-manifest-v1.schema.json").read_text())
    v2 = json.loads(schemas.joinpath("catalog-manifest-v2.schema.json").read_text())
    assert v1["properties"]["schema_version"]["const"] == "data-catalog-v1"
    assert v2["properties"]["schema_version"]["const"] == "data-catalog-v2"


def test_validate_v3_manifest_accepts_v3_without_changing_legacy_dispatch():
    from data_engineering.catalog import validate_manifest
    catalog = _v3_catalog_module()

    catalog.validate_v3_manifest(_v3_manifest())
    with pytest.raises(ValueError, match="unsupported catalog manifest schema"):
        validate_manifest(_v3_manifest())


def test_validate_v3_manifest_rejects_empty_or_reversed_partition_interval():
    catalog = _v3_catalog_module()

    manifest = _v3_manifest()
    manifest["partitions"][0]["end"] = "2024-12-31T00:00:00Z"
    with pytest.raises(ValueError, match="partition interval"):
        catalog.validate_v3_manifest(manifest)


def test_register_dataset_is_idempotent_when_registration_time_changes(tmp_path):
    catalog = _v3_catalog_module()

    manifest = _v3_manifest()
    first = catalog.register_dataset(tmp_path, manifest)
    changed_timestamp = deepcopy(manifest)
    changed_timestamp["registered_at"] = "2026-10-21T00:00:00Z"
    second = catalog.register_dataset(tmp_path, changed_timestamp)

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["manifest"] == second["manifest"]
    assert len(list((tmp_path / "catalog-v3" / "datasets" / manifest["dataset_id"]).glob("*.json"))) == 1


def test_register_dataset_appends_revised_snapshot_and_preserves_prior_version(tmp_path):
    catalog = _v3_catalog_module()

    first_manifest = _v3_manifest()
    first = catalog.register_dataset(tmp_path, first_manifest)
    second_manifest = deepcopy(first_manifest)
    second_manifest["registered_at"] = "2026-10-21T00:00:00Z"
    second_manifest["partitions"][0]["source_sha256"] = "c" * 64
    second_manifest["partitions"][0]["fingerprint"] = "d" * 64
    second_manifest["revision"]["previous_manifest_sha256"] = first["manifest_sha256"]
    second_manifest["revision"]["reason"] = "source correction"
    second = catalog.register_dataset(tmp_path, second_manifest)

    versions = catalog.list_datasets(tmp_path)
    version_files = list(
        (tmp_path / "catalog-v3" / "datasets" / first_manifest["dataset_id"]).glob("*.json")
    )
    assert first["manifest_sha256"] != second["manifest_sha256"]
    assert len(versions) == 2
    assert len(version_files) == 2
    assert second["manifest"]["revision"]["previous_manifest_sha256"] == first["manifest_sha256"]


def test_register_dataset_rejects_revision_not_linked_to_previous_version(tmp_path):
    catalog = _v3_catalog_module()

    first_manifest = _v3_manifest()
    catalog.register_dataset(tmp_path, first_manifest)
    changed = deepcopy(first_manifest)
    changed["partitions"][0]["fingerprint"] = "e" * 64
    changed["revision"]["previous_manifest_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="revision does not reference latest manifest"):
        catalog.register_dataset(tmp_path, changed)


def test_list_datasets_returns_stable_order_and_applies_identity_filters(tmp_path):
    catalog = _v3_catalog_module()

    for asset in ("ETH", "BTC"):
        manifest = _v3_manifest()
        manifest["asset"] = asset
        manifest["dataset_id"] = f"binance-futures-{asset.lower()}-ohlcv"
        manifest["exchange_symbol"] = f"{asset}/USDT:USDT"
        catalog.register_dataset(tmp_path, manifest)

    listed = catalog.list_datasets(tmp_path)
    btc = catalog.list_datasets(tmp_path, asset="BTC", market_type="futures", data_kind="ohlcv")
    assert [item["manifest"]["asset"] for item in listed] == ["BTC", "ETH"]
    assert len(btc) == 1
    assert btc[0]["manifest"]["dataset_id"] == "binance-futures-btc-ohlcv"
