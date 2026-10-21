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


def run_protocol(reader, writer, initial=None) -> None:
    init = initial if initial is not None else json.loads(reader.readline())
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


V2_PROTOCOL_VERSION = "2.0"
V2_MAX_FRAME_BYTES = 16 * 1024 * 1024
V2_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
V2_MAX_STEPS = 512
V2_MAX_STATE_BYTES = 1024 * 1024
V2_ALLOWED_FIELDS = {
    "open_time", "close_time", "available_time", "decision_time", "symbol", "eligible",
    "open", "high", "low", "close", "volume",
    "spot_ohlcv_open", "spot_ohlcv_high", "spot_ohlcv_low", "spot_ohlcv_close", "spot_ohlcv_volume",
    "spot_ohlcv_is_available", "futures_ohlcv_open", "futures_ohlcv_high", "futures_ohlcv_low",
    "futures_ohlcv_close", "futures_ohlcv_volume", "futures_ohlcv_is_available",
    "spot_funding_rate", "spot_funding_rate_is_available", "futures_funding_rate",
    "futures_funding_rate_is_available", "futures_open_interest", "futures_open_interest_is_available",
}
V2_NUMERIC_FIELDS = {
    "open", "high", "low", "close", "volume", "spot_ohlcv_open", "spot_ohlcv_high",
    "spot_ohlcv_low", "spot_ohlcv_close", "spot_ohlcv_volume", "futures_ohlcv_open",
    "futures_ohlcv_high", "futures_ohlcv_low", "futures_ohlcv_close", "futures_ohlcv_volume",
    "spot_funding_rate", "futures_funding_rate", "futures_open_interest",
}
V2_BOOLEAN_FIELDS = {
    "eligible", "spot_ohlcv_is_available", "futures_ohlcv_is_available",
    "spot_funding_rate_is_available", "futures_funding_rate_is_available",
    "futures_open_interest_is_available",
}


def _validate_state_v2(state, schema_id, schema_version):
    if schema_id != "json-object-v1" or schema_version != "1":
        raise ValueError("unknown state schema")
    if not isinstance(state, dict):
        raise ValueError("state must be a JSON object")

    def normalize(value, depth=0):
        if depth > 16:
            raise ValueError("state nesting is too deep")
        if value is None or type(value) in {str, bool, int}:
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("state must be finite")
            return value
        if type(value) is list:
            return [normalize(item, depth + 1) for item in value]
        if type(value) is dict:
            if any(not isinstance(key, str) or not key or len(key) > 256 for key in value):
                raise ValueError("state keys must be bounded strings")
            return {key: normalize(item, depth + 1) for key, item in value.items()}
        raise ValueError("state contains non-JSON values")

    normalized = normalize(state)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > V2_MAX_STATE_BYTES:
        raise ValueError("state exceeds size limit")
    return json.loads(encoded)


def _frame_from_v2_wire(rows: list[dict]) -> pd.DataFrame:
    if not isinstance(rows, list):
        raise ValueError("v2 history must be a list")
    if rows:
        frame = pd.DataFrame.from_records(rows)
    else:
        frame = pd.DataFrame(columns=["open_time", "symbol", "available_time"])
    if not {"open_time", "symbol", "available_time"}.issubset(frame.columns):
        raise ValueError("v2 history missing required PIT columns")
    unknown = set(frame.columns) - V2_ALLOWED_FIELDS
    if unknown:
        raise ValueError("v2 history has unregistered fields")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    frame["available_time"] = pd.to_datetime(frame["available_time"], utc=True, errors="raise")
    return frame.set_index(["open_time", "symbol"]).sort_index(kind="mergesort")


def _validate_history_v2(history: pd.DataFrame, *, as_of, symbols: list[str], allow_empty=False) -> pd.DataFrame:
    if not isinstance(history.index, pd.MultiIndex) or list(history.index.names) != ["open_time", "symbol"]:
        raise ValueError("v2 history index must be (open_time, symbol)")
    if not history.index.is_unique or not history.index.is_monotonic_increasing:
        raise ValueError("v2 history must be sorted and unique")
    if history.empty:
        if allow_empty:
            return history
        raise ValueError("v2 evaluation history is empty")
    if not set(history.columns).issubset(V2_ALLOWED_FIELDS - {"open_time", "symbol"}):
        raise ValueError("v2 history has unregistered fields")
    for name in set(history.columns) & V2_NUMERIC_FIELDS:
        for value in history[name]:
            if pd.isna(value):
                continue
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not math.isfinite(float(value)):
                raise ValueError("v2 feature values must be finite numbers or null")
    for name in set(history.columns) & V2_BOOLEAN_FIELDS:
        if any(not pd.isna(value) and not isinstance(value, (bool, np.bool_)) for value in history[name]):
            raise ValueError("v2 feature values must be booleans or null")
    cutoff = _utc(as_of)
    opened = history.index.get_level_values("open_time")
    if opened.tz is None or str(opened.tz) != "UTC":
        raise ValueError("v2 open_time must be UTC")
    available = pd.to_datetime(history["available_time"], utc=True, errors="raise")
    if (opened > cutoff).any() or (available > cutoff).any() or (available < opened).any():
        raise ValueError("v2 history violates point-in-time availability")
    if "decision_time" in history:
        decisions = pd.to_datetime(history.decision_time, utc=True, errors="raise")
        if (decisions > cutoff).any():
            raise ValueError("v2 decision time exceeds as_of")
    got = set(history.index.get_level_values("symbol"))
    if not got.issubset(set(symbols)):
        raise ValueError("v2 history contains undeclared symbols")
    for _, section in history.groupby(level="open_time", sort=False):
        if set(section.index.get_level_values("symbol")) != set(symbols):
            raise ValueError("v2 history has missing or shifted cross-asset rows")
    return history


def _validate_init_v2(init):
    expected = {
        "type", "protocol_version", "plan_hash", "source", "params", "symbols",
        "schema_id", "schema_version", "execution_mode", "state_schema_id",
        "state_schema_version", "state",
    }
    if not isinstance(init, dict) or set(init) != expected or init.get("type") != "init":
        raise ValueError("invalid v2 init frame")
    if (init["protocol_version"] != V2_PROTOCOL_VERSION or not isinstance(init["plan_hash"], str)
            or len(init["plan_hash"]) != 64 or any(char not in "0123456789abcdef" for char in init["plan_hash"])):
        raise ValueError("unsupported v2 protocol or plan hash")
    source = init["source"]
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds v2 limit")
    if init["schema_id"] != "ohlcv-v1" or init["schema_version"] != "1":
        raise ValueError("unknown input schema")
    symbols, params = init["symbols"], init["params"]
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS
            or any(not isinstance(symbol, str) or not symbol for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("invalid declared symbols")
    if not isinstance(params, dict) or len(params) > 64 or any(
        not isinstance(key, str) or not key or len(key) > 64 or value is None or isinstance(value, (list, dict))
        or isinstance(value, float) and not math.isfinite(value)
        for value in params.values()
    ):
        raise ValueError("parameters must be finite JSON scalars")
    mode = init["execution_mode"]
    if mode not in {"stateless", "stateful"}:
        raise ValueError("unknown execution mode")
    if mode == "stateful":
        if init["state_schema_id"] != "json-object-v1" or init["state_schema_version"] != "1":
            raise ValueError("unknown state schema")
        if init["state"] is not None:
            init["state"] = _validate_state_v2(init["state"], init["state_schema_id"], init["state_schema_version"])
    elif any(init[key] is not None for key in ("state_schema_id", "state_schema_version", "state")):
        raise ValueError("stateless execution cannot carry state")


def run_protocol_v2(reader, writer, initial=None) -> None:
    init = initial if initial is not None else json.loads(reader.readline(V2_MAX_FRAME_BYTES + 1))
    _validate_init_v2(init)
    symbols, params = init["symbols"], init["params"]
    stateful = init["execution_mode"] == "stateful"
    random.seed(0)
    np.random.seed(0)
    namespace: dict[str, Any] = {"__name__": "submitted_factor"}
    exec(compile(init["source"], "<submitted-factor-v2>", "exec"), namespace)
    factor = namespace.get("compute_factor")
    if not callable(factor):
        raise ValueError("factor must define compute_factor")
    writer.write(json.dumps({"type": "ready", "protocol_version": V2_PROTOCOL_VERSION}, separators=(",", ":")) + "\n")
    writer.flush()
    state = init["state"]
    input_bytes = 0
    output_bytes = 0
    step_count = 0
    warmup_seen = False
    previous_as_of = None
    for raw in reader:
        input_bytes += len(raw.encode("utf-8"))
        if input_bytes > V2_MAX_FRAME_BYTES:
            raise ValueError("v2 input exceeds byte limit")
        frame = json.loads(raw)
        if frame.get("type") == "warmup":
            if warmup_seen or set(frame) != {"type", "phase", "as_of", "history"}:
                raise ValueError("invalid or duplicate warmup frame")
            warmup_seen = True
            phase = frame["phase"]
            if phase not in {"initialize", "read_only"}:
                raise ValueError("invalid warmup phase")
            warmup_time = _utc(frame["as_of"])
            warmup_history = _validate_history_v2(
                _frame_from_v2_wire(frame["history"]), as_of=warmup_time,
                symbols=symbols, allow_empty=True,
            )
            if stateful:
                if (state is None) != (phase == "initialize"):
                    raise ValueError("warmup phase does not match state checkpoint")
                if state is None:
                    initializer = namespace.get("initialize_state")
                    state = initializer(warmup_history, params=params) if callable(initializer) else {}
                    state = _validate_state_v2(state, init["state_schema_id"], init["state_schema_version"])
            continue
        if frame.get("type") == "end":
            if frame != {"type": "end"} or not warmup_seen or step_count == 0:
                raise ValueError("invalid v2 end frame")
            if stateful:
                checkpoint = {"type": "checkpoint", "plan_hash": init["plan_hash"], "state": state}
                line = json.dumps(checkpoint, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                output_bytes += len(line.encode("utf-8"))
                if output_bytes > V2_MAX_OUTPUT_BYTES:
                    raise ValueError("v2 output exceeds byte limit")
                writer.write(line)
                writer.flush()
            if reader.read().strip():
                raise ValueError("Worker received frames after end")
            return
        if frame.get("type") != "step" or set(frame) != {"type", "as_of", "history"}:
            raise ValueError("invalid v2 step frame")
        if not warmup_seen:
            raise ValueError("v2 evaluation must follow warmup frame")
        step_count += 1
        if step_count > V2_MAX_STEPS:
            raise ValueError("v2 invocation exceeds step limit")
        as_of = _utc(frame["as_of"])
        if previous_as_of is not None and as_of <= previous_as_of:
            raise ValueError("v2 step timestamps must be strictly increasing")
        previous_as_of = as_of
        history = _validate_history_v2(
            _frame_from_v2_wire(frame["history"]), as_of=as_of, symbols=symbols,
        )
        if stateful:
            result = factor(history, as_of=as_of, params=params, state=state)
            if not isinstance(result, tuple) or len(result) != 2:
                raise ValueError("stateful factor must return (values, next_state)")
            values, state = result
            state = _validate_state_v2(state, init["state_schema_id"], init["state_schema_version"])
        else:
            values = factor(history, as_of=as_of, params=params)
        values = normalize_output(values, symbols=symbols)
        response = {
            "type": "result", "plan_hash": init["plan_hash"],
            "as_of": as_of.isoformat(), "values": values,
        }
        line = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        output_bytes += len(line.encode("utf-8"))
        if output_bytes > V2_MAX_OUTPUT_BYTES:
            raise ValueError("v2 output exceeds byte limit")
        writer.write(line)
        writer.flush()
    raise ValueError("v2 Worker input ended without end frame")


def dispatch_protocol(reader, writer):
    first = reader.readline(V2_MAX_FRAME_BYTES + 1)
    if not first:
        raise ValueError("missing init frame")
    init = json.loads(first)
    if init.get("protocol_version") == V2_PROTOCOL_VERSION:
        run_protocol_v2(reader, writer, init)
    else:
        run_protocol(reader, writer, init)


if __name__ == "__main__":
    try:
        dispatch_protocol(sys.stdin, sys.stdout)
    except Exception as exc:
        # Error text can contain source-controlled values; expose only a stable category.
        sys.stdout.write(json.dumps({"type": "worker_error", "category": type(exc).__name__}) + "\n")
        sys.stdout.flush()
        raise SystemExit(2)
