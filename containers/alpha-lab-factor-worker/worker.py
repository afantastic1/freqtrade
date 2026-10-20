"""Untrusted-code harness. This module is copied into and run only by the Worker image."""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

MAX_DIAGNOSTICS_BYTES = 4096
MAX_SYMBOLS = 100
MAX_SOURCE_BYTES = 256 * 1024


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.tz_convert("UTC")


def validate_history(history: pd.DataFrame, *, as_of: datetime | pd.Timestamp,
                     symbols: list[str]) -> pd.DataFrame:
    if not isinstance(history.index, pd.MultiIndex) or list(history.index.names) != ["open_time", "symbol"]:
        raise ValueError("history index must be (open_time, symbol)")
    if not history.index.is_unique:
        raise ValueError("history index must be unique")
    if not history.index.is_monotonic_increasing:
        raise ValueError("history must be sorted")
    if "available_time" not in history.columns:
        raise ValueError("history requires available_time")
    if not set(history.columns).issubset({"available_time", "open", "high", "low", "close", "volume"}):
        raise ValueError("history columns must be allowlisted market features")
    if not isinstance(history.index.get_level_values("open_time"), pd.DatetimeIndex):
        raise ValueError("open_time must be a UTC DatetimeIndex")
    times = history.index.get_level_values("open_time")
    if times.tz is None or str(times.tz) != "UTC":
        raise ValueError("open_time must be UTC")
    available = pd.to_datetime(history["available_time"], utc=True, errors="raise")
    cutoff = _utc(as_of)
    if available.isna().any() or (available > cutoff).any():
        raise ValueError("history contains data not yet available at as_of")
    if (times > cutoff).any():
        raise ValueError("history contains an open_time after as_of")
    if not symbols or len(symbols) > MAX_SYMBOLS or len(set(symbols)) != len(symbols):
        raise ValueError("invalid declared symbols")
    got = set(history.index.get_level_values("symbol"))
    if not got.issubset(set(symbols)):
        raise ValueError("history contains undeclared symbols")
    for _, section in history.groupby(level="open_time", sort=False):
        section_symbols = set(section.index.get_level_values("symbol"))
        if section_symbols != set(symbols):
            raise ValueError("history has missing or shifted cross-asset rows")
    if list(history.index) != sorted(history.index.tolist()):
        raise ValueError("history must be sorted")
    return history


def normalize_output(output: Any, *, symbols: list[str], diagnostics: dict | None = None) -> dict[str, float]:
    if isinstance(output, pd.Series):
        values = output.to_dict()
    elif isinstance(output, dict):
        values = output
    else:
        raise ValueError("factor output must be a Series or symbol mapping")
    if set(values) != set(symbols) or len(values) != len(symbols):
        raise ValueError("factor output symbols must match declaration")
    normalized: dict[str, float] = {}
    for symbol in symbols:
        value = values[symbol]
        if isinstance(value, (bool, np.bool_)) or not np.isscalar(value):
            raise ValueError("factor output values must be numeric scalars")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("factor output values must be finite")
        normalized[symbol] = number
    safe_diagnostics = diagnostics or {}
    if not isinstance(safe_diagnostics, dict) or any(
        not isinstance(value, (str, int, float, bool)) for value in safe_diagnostics.values()
    ):
        raise ValueError("diagnostics must be scalar values")
    if len(json.dumps(safe_diagnostics, allow_nan=False).encode("utf-8")) > MAX_DIAGNOSTICS_BYTES:
        raise ValueError("diagnostics exceed size limit")
    return normalized


def _frame_from_wire(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows)
    if not {"open_time", "symbol", "available_time"}.issubset(frame.columns):
        raise ValueError("wire history missing required columns")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    frame["available_time"] = pd.to_datetime(frame["available_time"], utc=True, errors="raise")
    return frame.set_index(["open_time", "symbol"]).sort_index(kind="mergesort")


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_protocol(reader, writer) -> None:
    init = json.loads(reader.readline())
    if init.get("type") != "init" or init.get("protocol_version") != "1.0":
        raise ValueError("invalid init frame")
    source = init["source"]
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds limit")
    symbols = init["symbols"]
    if not isinstance(symbols, list) or len(symbols) > MAX_SYMBOLS:
        raise ValueError("invalid symbols")
    params = init.get("params", {})
    seed = int(init.get("seed", 0))
    random.seed(seed)
    np.random.seed(seed)
    namespace: dict[str, Any] = {"__name__": "submitted_factor"}
    exec(compile(source, "<submitted-factor>", "exec"), namespace)  # Worker namespace only.
    factor = namespace.get("compute_factor")
    if not callable(factor):
        raise ValueError("factor must define compute_factor")
    writer.write(json.dumps({"type": "ready", "protocol_version": "1.0"}, separators=(",", ":")) + "\n")
    writer.flush()
    for raw in reader:
        frame = json.loads(raw)
        if frame.get("type") == "end":
            return
        if frame.get("type") != "step":
            raise ValueError("invalid step frame")
        as_of = _utc(frame["as_of"])
        history = validate_history(_frame_from_wire(frame["history"]), as_of=as_of, symbols=symbols)
        result = factor(history, as_of=as_of, params=params)
        values = normalize_output(result, symbols=symbols)
        response = {"as_of": as_of.isoformat(), "values": values, "diagnostics": {}, "output_hash": ""}
        response["output_hash"] = _canonical_hash(response)
        writer.write(json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        writer.flush()


if __name__ == "__main__":
    try:
        run_protocol(sys.stdin, sys.stdout)
    except Exception as exc:
        # Error text can contain source-controlled values; expose only a stable category.
        sys.stdout.write(json.dumps({"type": "worker_error", "category": type(exc).__name__}) + "\n")
        sys.stdout.flush()
        raise SystemExit(2)
