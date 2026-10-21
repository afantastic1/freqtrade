import json

import pytest

from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY, serialize_typed_rows


def test_registry_exposes_only_registered_pr04_worker_columns():
    schema = INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1")
    assert {"open", "high", "low", "close", "volume"}.issubset(schema.fields)
    assert "label_value" not in schema.fields
    with pytest.raises(ValueError, match="unknown"):
        schema.validate_fields({"close", "label_value"})


def test_registry_has_typed_availability_and_null_semantics():
    schema = INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1")
    close = schema.fields["close"]
    assert close.logical_type == "number"
    assert close.source_data_type == "futures_ohlcv"
    assert close.availability_rule == "available_time<=as_of"
    assert close.null_policy == "preserve-null"


def test_typed_rows_are_canonical_and_keep_nulls():
    rows = [{"available_time": "2025-01-01T00:05:00Z", "close": None, "symbol": "BTC/USDT"}]
    encoded = serialize_typed_rows(rows, INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1"))
    assert encoded == json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert b'"close":null' in encoded


def test_typed_rows_reject_nonfinite_numbers_and_unregistered_fields():
    schema = INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1")
    with pytest.raises(ValueError):
        serialize_typed_rows([{"available_time": "2025-01-01T00:05:00Z", "close": float("inf")}], schema)
    with pytest.raises(ValueError, match="unknown"):
        serialize_typed_rows([{"label_value": 1.0}], schema)
