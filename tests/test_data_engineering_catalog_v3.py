import importlib.util
import importlib
from copy import deepcopy
import json
from importlib.resources import files

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
