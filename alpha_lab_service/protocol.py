"""Strict wire models for Python Factor Execution Protocol v1."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alpha_lab_service import PROTOCOL_VERSION
from alpha_lab.version import ENGINE_VERSION

MAX_SOURCE_BYTES = 256 * 1024
MAX_SYMBOLS = 100
MAX_DIAGNOSTICS_BYTES = 4096


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be an ISO-8601 string")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return dt.astimezone(timezone.utc)


class FactorRequest(BaseModel):
    """Client-controlled declaration; deliberately contains no path or runtime fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    hypothesis: str = Field(min_length=1, max_length=2000)
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    timeframe: str = Field(pattern=r"^(1m|5m|15m|1h|4h|1d)$")
    start: datetime
    end: datetime
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=64)
    max_lookback: int = Field(ge=1, le=10000)

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, value: Any) -> str:
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("source must be UTF-8") from exc
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source must be non-empty UTF-8 text")
        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds byte limit")
        return value

    @field_validator("start", "end", mode="before")
    @classmethod
    def validate_timezones(cls, value: Any) -> datetime:
        return _timestamp(value)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, value: list[str]) -> list[str]:
        if any(not symbol or len(symbol) > 64 for symbol in value):
            raise ValueError("symbols must be non-empty strings of at most 64 characters")
        if len(set(value)) != len(value):
            raise ValueError("symbols must be unique")
        return value

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if not key or len(key) > 64 or isinstance(item, (list, dict)) or item is None:
                raise ValueError("parameters must be named JSON scalar values")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("parameter floats must be finite")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "FactorRequest":
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self


class FactorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    values: dict[str, float] = Field(min_length=1, max_length=MAX_SYMBOLS)
    diagnostics: dict[str, str | int | float | bool] = Field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION
    output_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("as_of", mode="before")
    @classmethod
    def validate_as_of(cls, value: Any) -> datetime:
        return _timestamp(value)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(float(item)) for item in value.values()):
            raise ValueError("factor values must be finite")
        return value

    @field_validator("diagnostics")
    @classmethod
    def bound_diagnostics(cls, value: dict[str, Any]) -> dict[str, Any]:
        if any(not isinstance(item, (str, int, float, bool)) for item in value.values()):
            raise ValueError("diagnostics must be scalar values")
        try:
            size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("diagnostics must be finite JSON scalars") from exc
        if size > MAX_DIAGNOSTICS_BYTES:
            raise ValueError("diagnostics exceed byte limit")
        return value

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        if value != PROTOCOL_VERSION:
            raise ValueError("unsupported factor output protocol version")
        return value


def canonical_request_hash(
    request: FactorRequest,
    *,
    data_fingerprint: str,
    image_digest: str,
    engine_identity: str,
    statistical_method_identity: str = "descriptive-only-v1;formal-inference=BLOCKED",
    data_availability_semantics: str = "available_time<=as_of;UTC",
) -> str:
    """Hash every user declaration and execution identity input deterministically."""
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "engine_version": ENGINE_VERSION,
        "request": request.model_dump(mode="json"),
        "data_fingerprint": data_fingerprint,
        "data_availability_semantics": data_availability_semantics,
        "image_digest": image_digest,
        "engine_identity": engine_identity,
        "statistical_method_identity": statistical_method_identity,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
