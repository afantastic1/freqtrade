"""Strict v2 Broker frame and bounded checkpoint validation."""

from __future__ import annotations

import hashlib
import json
import math
import re

import pandas as pd

from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY, serialize_typed_rows
from alpha_lab_service.protocol import MAX_SOURCE_BYTES
from alpha_lab_service.protocol_v2 import V2_LIMITS, V2_PROTOCOL_VERSION

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_SYMBOLS = 100
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATE_SCHEMA_REGISTRY_V2 = {("json-object-v1", "1")}


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _json_value(value, depth=0):
    if depth > 16:
        raise ValueError("state exceeds maximum JSON nesting depth")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("state values must be finite")
        return value
    if type(value) is list:
        return [_json_value(item, depth + 1) for item in value]
    if type(value) is dict:
        if any(not isinstance(key, str) or not key or len(key) > 256 for key in value):
            raise ValueError("state object keys must be bounded strings")
        return {key: _json_value(item, depth + 1) for key, item in value.items()}
    raise ValueError("state must contain only canonical JSON values")


def validate_state_schema(schema_id: str, schema_version: str) -> None:
    if (schema_id, schema_version) not in STATE_SCHEMA_REGISTRY_V2:
        raise ValueError("unknown state schema or version")


def validate_state_payload(state, schema_id: str, schema_version: str) -> dict:
    validate_state_schema(schema_id, schema_version)
    if not isinstance(state, dict):
        raise ValueError("state schema requires a JSON object")
    normalized = _json_value(state)
    encoded = _canonical(normalized)
    if len(encoded) > V2_LIMITS.max_state_bytes:
        raise ValueError("state exceeds size limit")
    return json.loads(encoded)


def state_hash(state, schema_id: str, schema_version: str) -> str:
    normalized = validate_state_payload(state, schema_id, schema_version)
    return hashlib.sha256(_canonical(normalized)).hexdigest()


def _validate_history(rows, *, schema, symbols, cutoff, allow_empty=False):
    if not isinstance(rows, list) or (not rows and not allow_empty):
        raise ValueError("Worker history must be a non-empty row list")
    serialize_typed_rows(rows, schema)
    seen = set()
    sections: dict[str, set[str]] = {}
    ordered = []
    for row in rows:
        if not {"open_time", "available_time", "symbol"}.issubset(row):
            raise ValueError("Worker row missing PIT timestamp or symbol")
        opened, available = pd.Timestamp(row["open_time"]), pd.Timestamp(row["available_time"])
        if opened.tzinfo is None or available.tzinfo is None:
            raise ValueError("Worker row timestamps must be aware")
        opened, available = opened.tz_convert("UTC"), available.tz_convert("UTC")
        if opened > cutoff or available > cutoff or available < opened:
            raise ValueError("Worker row violates point-in-time bounds")
        symbol = row["symbol"]
        key = (opened, symbol)
        if symbol not in symbols or key in seen:
            raise ValueError("Worker row has undeclared or duplicate symbol")
        seen.add(key)
        sections.setdefault(row["open_time"], set()).add(symbol)
        ordered.append(key)
        if "decision_time" in row:
            decision = pd.Timestamp(row["decision_time"])
            if decision.tzinfo is None or decision.tz_convert("UTC") > cutoff:
                raise ValueError("Worker decision row is unavailable at cutoff")
    if ordered != sorted(ordered) or any(values != set(symbols) for values in sections.values()):
        raise ValueError("Worker history must be sorted and cross-asset aligned")


def validate_worker_frames_v2(task_id: str, chunk_id: str, frames: list[dict]) -> None:
    if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
        raise ValueError("invalid task id")
    if not isinstance(chunk_id, str) or not _ID.fullmatch(chunk_id):
        raise ValueError("invalid chunk id")
    if not isinstance(frames, list) or not 4 <= len(frames) <= V2_LIMITS.max_steps_per_chunk + 3:
        raise ValueError("v2 Worker frames outside limits")
    init, warmup, *steps, end = frames
    init_keys = {
        "type", "protocol_version", "plan_hash", "source", "params", "symbols",
        "schema_id", "schema_version", "execution_mode", "state_schema_id",
        "state_schema_version", "state",
    }
    if not isinstance(init, dict) or set(init) != init_keys or init.get("type") != "init":
        raise ValueError("invalid v2 init frame")
    if init["protocol_version"] != V2_PROTOCOL_VERSION or not _SHA256.fullmatch(str(init["plan_hash"])):
        raise ValueError("invalid v2 protocol or chunk plan identity")
    source = init["source"]
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds v2 limit")
    symbols = init["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS or any(not isinstance(s, str) or not s for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("invalid v2 symbol declaration")
    params = init["params"]
    if not isinstance(params, dict) or len(params) > 64 or any(
        not isinstance(key, str) or not key or len(key) > 64 or value is None
        or isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value)
        for key, value in params.items()
    ):
        raise ValueError("v2 parameters must be bounded finite JSON scalars")
    if init["execution_mode"] not in {"stateless", "stateful"}:
        raise ValueError("unknown execution mode")
    stateful = init["execution_mode"] == "stateful"
    if stateful:
        validate_state_schema(init["state_schema_id"], init["state_schema_version"])
        if init["state"] is not None:
            validate_state_payload(init["state"], init["state_schema_id"], init["state_schema_version"])
    elif any(init[key] is not None for key in ("state_schema_id", "state_schema_version", "state")):
        raise ValueError("stateless execution cannot carry state")
    schema = INPUT_SCHEMA_REGISTRY.get(init["schema_id"], init["schema_version"])
    if not isinstance(warmup, dict) or set(warmup) != {"type", "phase", "as_of", "history"} or warmup.get("type") != "warmup":
        raise ValueError("invalid v2 warmup frame")
    if warmup["phase"] not in {"initialize", "read_only"}:
        raise ValueError("invalid warmup phase")
    if stateful and ((init["state"] is None) != (warmup["phase"] == "initialize")):
        raise ValueError("state and warmup phase do not match chunk continuation")
    warmup_time = pd.Timestamp(warmup["as_of"])
    if warmup_time.tzinfo is None:
        raise ValueError("warmup timestamp must be aware")
    warmup_time = warmup_time.tz_convert("UTC")
    _validate_history(warmup["history"], schema=schema, symbols=symbols, cutoff=warmup_time, allow_empty=True)
    previous = None
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"type", "as_of", "history"} or step.get("type") != "step":
            raise ValueError("invalid v2 evaluation step")
        timestamp = pd.Timestamp(step["as_of"])
        if timestamp.tzinfo is None:
            raise ValueError("step timestamp must be aware")
        timestamp = timestamp.tz_convert("UTC")
        if previous is not None and timestamp <= previous:
            raise ValueError("v2 evaluation timestamps must be strictly increasing")
        previous = timestamp
        _validate_history(step["history"], schema=schema, symbols=symbols, cutoff=timestamp)
    if not isinstance(end, dict) or end != {"type": "end"}:
        raise ValueError("invalid v2 end frame")
    if len(_canonical(frames)) > MAX_FRAME_BYTES:
        raise ValueError("v2 Worker input exceeds frame byte limit")
