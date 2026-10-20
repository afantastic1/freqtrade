"""Non-mutating validation for observations stored in the v3 catalog."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from ..data_kind_registry import DATA_KIND_CONTRACTS

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _timeframe_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.fullmatch(r"([1-9][0-9]*)([smhdw])", value)
    if not match:
        raise ValueError(f"unsupported timeframe: {value}")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def validate_observations(
    frame: pd.DataFrame, *, data_kind: str, timeframe: str | None
) -> dict[str, Any]:
    """Return a quality summary without changing or repairing ``frame``."""
    contract = DATA_KIND_CONTRACTS.get(data_kind)
    if contract is None:
        raise ValueError(f"unsupported data kind: {data_kind}")
    issues: list[dict[str, str]] = []

    def issue(code: str, message: str) -> None:
        if not any(item["code"] == code for item in issues):
            issues.append({"code": code, "message": message})

    count = int(len(frame))
    cadence: dict[str, Any] = {"expected_observations": None, "missing_observations": 0}
    interval: dict[str, str] | None = None
    if frame.empty:
        return {"row_count": 0, "observed_interval": None, "cadence": cadence,
                "status": "MISSING", "issues": []}
    missing = contract["required_fields"] - set(frame.columns)
    if missing:
        issue("MISSING_REQUIRED_FIELD", f"missing required fields: {sorted(missing)}")
    if contract["one_of_fields"] and not (contract["one_of_fields"] & set(frame.columns)):
        issue("MISSING_REQUIRED_FIELD", f"requires one of: {sorted(contract['one_of_fields'])}")

    date_values: list[pd.Timestamp] = []
    if contract["timestamp_field"] in frame:
        for value in frame[contract["timestamp_field"]].tolist():
            try:
                stamp = pd.Timestamp(value)
            except (TypeError, ValueError):
                issue("INVALID_TIMESTAMP", "timestamp cannot be parsed")
                continue
            if pd.isna(stamp):
                issue("INVALID_TIMESTAMP", "timestamp is null")
                continue
            if stamp.tzinfo is None:
                issue("NAIVE_TIMESTAMP", "timestamps must include a timezone")
                continue
            if stamp.utcoffset().total_seconds() != 0:
                issue("NON_UTC_TIMESTAMP", "timestamps must be expressed in UTC")
                continue
            date_values.append(stamp.tz_convert("UTC"))
    if len(date_values) == count:
        stamps = pd.DatetimeIndex(date_values)
        interval = {"start": stamps.min().isoformat().replace("+00:00", "Z"),
                    "end": stamps.max().isoformat().replace("+00:00", "Z")}
        if stamps.has_duplicates:
            issue("DUPLICATE_TIMESTAMP", "timestamps must be unique")
        if not stamps.is_monotonic_increasing:
            issue("UNSORTED_TIMESTAMP", "timestamps must be increasing")
        step = _timeframe_seconds(timeframe)
        if step is not None and contract["cadence_policy"] == "dense":
            step_ns = step * 1_000_000_000
            if any(int(value) % step_ns for value in stamps.as_unit("ns").asi8):
                issue("TIMEFRAME_MISALIGNED", "timestamps do not align to the timeframe")
            if contract["cadence_policy"] == "dense":
                expected = int((stamps.max().value - stamps.min().value) // (step * 1_000_000_000)) + 1
                cadence["expected_observations"] = expected
                cadence["missing_observations"] = max(0, expected - count)

    numeric_fields: set[str] = set()
    if data_kind == "ohlcv":
        numeric_fields = {"open", "high", "low", "close", "volume"}
    elif data_kind == "funding_rate":
        numeric_fields = {"funding_rate"}
    elif data_kind == "open_interest":
        numeric_fields = contract["one_of_fields"] & set(frame.columns)
    elif data_kind == "mark_price":
        numeric_fields = {"mark_price"}
    for column in numeric_fields & set(frame.columns):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not all(math.isfinite(float(value)) for value in values.dropna()):
            issue("NONFINITE_VALUE", f"{column} must contain finite numeric values")
            continue
        if data_kind in {"ohlcv", "mark_price"} and column in ({"open", "high", "low", "close"} if data_kind == "ohlcv" else {"mark_price"}):
            if (values <= 0).any():
                issue("NONPOSITIVE_PRICE", f"{column} must be positive")
        if data_kind == "ohlcv" and column == "volume" and (values < 0).any():
            issue("NEGATIVE_VOLUME", "volume must be nonnegative")
        if data_kind == "open_interest" and (values < 0).any():
            issue("NEGATIVE_OPEN_INTEREST", f"{column} must be nonnegative")
    if data_kind == "ohlcv" and {"open", "high", "low", "close"} <= set(frame.columns):
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        open_ = pd.to_numeric(frame["open"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        if ((high < low) | (high < open_) | (high < close) | (low > open_) | (low > close)).any():
            issue("INVALID_OHLC_BOUNDS", "OHLC prices violate high/low bounds")

    status = "INVALID" if issues else "PARTIAL" if cadence["missing_observations"] else "AVAILABLE"
    return {"row_count": count, "observed_interval": interval, "cadence": cadence,
            "status": status, "issues": issues}
