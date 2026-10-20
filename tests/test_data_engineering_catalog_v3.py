import importlib.util
import json
from importlib.resources import files

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
