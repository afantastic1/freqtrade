"""Trusted registry for typed, worker-safe DatasetBuilderV2 columns."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class WorkerFieldV1:
    name: str
    logical_type: str
    units: str
    source_data_type: str
    availability_rule: str
    null_policy: str
    shape: str


@dataclass(frozen=True)
class WorkerInputSchemaV1:
    schema_id: str
    version: str
    fields: Mapping[str, WorkerFieldV1]

    def __post_init__(self):
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    def validate_fields(self, names) -> None:
        unknown = set(names) - set(self.fields)
        if unknown:
            raise ValueError(f"unknown worker input fields: {sorted(unknown)}")


class WorkerInputSchemaRegistryV1:
    def __init__(self, schemas: Mapping[tuple[str, str], WorkerInputSchemaV1]):
        self._schemas = MappingProxyType(dict(schemas))

    def get(self, schema_id: str, version: str) -> WorkerInputSchemaV1:
        try:
            return self._schemas[(schema_id, version)]
        except KeyError as exc:
            raise ValueError("unknown worker input schema or version") from exc


def _field(name: str, logical_type: str, units: str, source: str) -> WorkerFieldV1:
    return WorkerFieldV1(name, logical_type, units, source, "available_time<=as_of", "preserve-null", "scalar-per-row")


_CORE_FIELDS = {
    "open_time": _field("open_time", "timestamp", "UTC", "futures_ohlcv"),
    "close_time": _field("close_time", "timestamp", "UTC", "futures_ohlcv"),
    "available_time": _field("available_time", "timestamp", "UTC", "futures_ohlcv"),
    "decision_time": _field("decision_time", "timestamp", "UTC", "futures_ohlcv"),
    "symbol": _field("symbol", "string", "instrument-id", "futures_ohlcv"),
    "eligible": _field("eligible", "boolean", "membership", "futures_ohlcv"),
}
_OHLCV_FIELDS = {name: _field(name, "number", unit, "futures_ohlcv") for name, unit in {
    "open": "price", "high": "price", "low": "price", "close": "price", "volume": "base-volume",
}.items()}
_AUX_FIELDS = {
    **{f"spot_ohlcv_{name}": _field(f"spot_ohlcv_{name}", "number", unit, "spot_ohlcv")
       for name, unit in {"open": "price", "high": "price", "low": "price", "close": "price", "volume": "base-volume"}.items()},
    "spot_ohlcv_is_available": _field("spot_ohlcv_is_available", "boolean", "availability", "spot_ohlcv"),
    **{f"futures_ohlcv_{name}": _field(f"futures_ohlcv_{name}", "number", unit, "futures_ohlcv")
       for name, unit in {"open": "price", "high": "price", "low": "price", "close": "price", "volume": "base-volume"}.items()},
    "futures_ohlcv_is_available": _field("futures_ohlcv_is_available", "boolean", "availability", "futures_ohlcv"),
    "spot_funding_rate": _field("spot_funding_rate", "number", "rate", "funding_rate"),
    "spot_funding_rate_is_available": _field("spot_funding_rate_is_available", "boolean", "availability", "funding_rate"),
    "futures_funding_rate": _field("futures_funding_rate", "number", "rate", "funding_rate"),
    "futures_funding_rate_is_available": _field("futures_funding_rate_is_available", "boolean", "availability", "funding_rate"),
    "futures_open_interest": _field("futures_open_interest", "number", "contracts", "open_interest"),
    "futures_open_interest_is_available": _field("futures_open_interest_is_available", "boolean", "availability", "open_interest"),
}
_OHLVC_SCHEMA = WorkerInputSchemaV1("ohlcv-v1", "1", {**_CORE_FIELDS, **_OHLCV_FIELDS, **_AUX_FIELDS})
INPUT_SCHEMA_REGISTRY = WorkerInputSchemaRegistryV1({("ohlcv-v1", "1"): _OHLVC_SCHEMA})


def serialize_typed_rows(rows: list[dict[str, Any]], schema: WorkerInputSchemaV1) -> bytes:
    """Validate registered scalar fields and return compact deterministic JSON bytes."""
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("worker rows must be objects")
        schema.validate_fields(row)
        for name, value in row.items():
            field = schema.fields[name]
            if value is None:
                if field.null_policy != "preserve-null":
                    raise ValueError(f"null is not allowed for {name}")
                continue
            if field.logical_type == "number" and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be a finite number or null")
            if field.logical_type == "boolean" and type(value) is not bool:
                raise ValueError(f"{name} must be a boolean or null")
            if field.logical_type in {"timestamp", "string"} and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
    try:
        return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("worker rows must be canonical finite JSON") from exc
