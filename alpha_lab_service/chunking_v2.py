"""Deterministic bounded chunk planning and PIT-safe v2 Worker framing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from bisect import insort
import hashlib
import json
import math
from typing import Any

import pandas as pd

from alpha_lab_service.authorization_v2 import AuthorizedFactorRunV2
from alpha_lab_service.data_feed import validate_v2_chunk_timeline
from alpha_lab_service.input_schemas_v2 import serialize_typed_rows
from alpha_lab_service.protocol_v2 import FactorRequestV2, V2_LIMITS, V2_PROTOCOL_VERSION


@dataclass(frozen=True)
class ChunkV2:
    chunk_id: str
    index: int
    plan_hash: str
    evaluation_timestamps: tuple[pd.Timestamp, ...]
    warmup_timestamps: tuple[pd.Timestamp, ...]
    input_bytes: int = 0
    input_hash: str = ""


@dataclass(frozen=True)
class ChunkPlanV2:
    identity_hash: str
    plan_hash: str
    chunks: tuple[ChunkV2, ...]
    total_steps: int
    total_input_bytes: int
    limits_version: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _iso(value) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("Worker timestamps must be timezone-aware")
    return stamp.tz_convert("UTC").isoformat()


def _json_value(value):
    if value is None or value is pd.NA or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return _iso(value)
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Worker inputs must contain finite numbers or null")
    return value


def _row_records(frame: pd.DataFrame, *, run: AuthorizedFactorRunV2, cutoff: pd.Timestamp) -> list[dict]:
    request = run.research_request
    schema = run.input_schema
    schema.validate_fields(frame.columns)
    rows = []
    for row in frame.to_dict(orient="records"):
        normalized = {name: _json_value(value) for name, value in row.items()}
        if "symbol" not in normalized and request.mode == "time_series" and len(request.symbols) == 1:
            # DatasetBuilder's time-series Worker product is scoped to one
            # trusted symbol and omits a redundant column; restore that identity
            # from the authorized request before producing wire frames.
            normalized["symbol"] = request.symbols[0]
        if not {"open_time", "available_time", "symbol"}.issubset(normalized):
            raise ValueError("PIT Worker rows require open_time, available_time, and symbol")
        opened, available = pd.Timestamp(normalized["open_time"]), pd.Timestamp(normalized["available_time"])
        if opened.tzinfo is None or available.tzinfo is None:
            raise ValueError("PIT Worker row timestamps must be timezone-aware")
        if opened.tz_convert("UTC") > cutoff or available.tz_convert("UTC") > cutoff:
            raise ValueError("Worker input contains a future or unavailable row")
        if available.tz_convert("UTC") < opened.tz_convert("UTC"):
            raise ValueError("Worker row availability precedes its open time")
        if normalized["symbol"] not in request.symbols:
            raise ValueError("Worker input contains an undeclared symbol")
        rows.append(normalized)
    rows.sort(key=lambda row: (row["open_time"], row["symbol"]))
    serialize_typed_rows(rows, schema)
    return rows


class _PITCursor:
    """Incrementally expose one trusted as-of snapshot at each cutoff."""

    def __init__(self, run: AuthorizedFactorRunV2, snapshot_end: pd.Timestamp):
        self.run = run
        snapshot = run.dataset.worker_input.asof(snapshot_end)
        rows = _row_records(snapshot, run=run, cutoff=snapshot_end)
        self._ready = []
        for ordinal, row in enumerate(rows):
            available = pd.Timestamp(row["available_time"]).tz_convert("UTC")
            decision = pd.Timestamp(row.get("decision_time", row["available_time"])).tz_convert("UTC")
            self._ready.append((max(available, decision).value, ordinal, row))
        self._ready.sort(key=lambda item: (item[0], item[1]))
        self._next = 0
        self._active: dict[str, list[tuple[int, dict]]] = {symbol: [] for symbol in run.research_request.symbols}
        self._seen: set[tuple[int, str]] = set()
        self._last_cutoff: pd.Timestamp | None = None

    def _advance(self, cutoff: pd.Timestamp) -> None:
        if self._last_cutoff is not None and cutoff < self._last_cutoff:
            raise ValueError("PIT cursor cutoffs must be ordered")
        while self._next < len(self._ready) and self._ready[self._next][0] <= cutoff.value:
            _ready_ns, _ordinal, row = self._ready[self._next]
            self._next += 1
            opened = pd.Timestamp(row["open_time"]).tz_convert("UTC")
            key = (opened.value, row["symbol"])
            if key in self._seen:
                raise ValueError("Worker input contains duplicate open_time/symbol rows")
            self._seen.add(key)
            insort(self._active[row["symbol"]], (opened.value, row))
        self._last_cutoff = cutoff

    def history_rows(self, cutoff: pd.Timestamp) -> list[dict]:
        self._advance(cutoff)
        rows = []
        for symbol in self.run.research_request.symbols:
            rows.extend(row for _opened, row in self._active[symbol][-self.run.research_request.lookback_bars:])
        rows.sort(key=lambda row: (row["open_time"], row["symbol"]))
        if not rows:
            raise ValueError("no authorized point-in-time rows available for evaluation")
        serialize_typed_rows(rows, self.run.input_schema)
        return rows

    def warmup_rows(self, cutoff: pd.Timestamp) -> list[dict]:
        self._advance(cutoff)
        rows = [
            row for entries in self._active.values() for _opened, row in entries
            if pd.Timestamp(row.get("decision_time", row["available_time"])).tz_convert("UTC") < cutoff
        ]
        if not rows:
            return []
        timestamps = sorted({pd.Timestamp(row.get("decision_time", row["available_time"])).tz_convert("UTC") for row in rows})
        selected = set(timestamps[-self.run.research_request.lookback_bars:])
        result = [row for row in rows if pd.Timestamp(row.get("decision_time", row["available_time"])).tz_convert("UTC") in selected]
        result.sort(key=lambda row: (row["open_time"], row["symbol"]))
        serialize_typed_rows(result, self.run.input_schema)
        return result


def build_chunk_frames(run: AuthorizedFactorRunV2, chunk: ChunkV2) -> list[dict]:
    """Build canonical frames exclusively from the trusted DatasetBuilder product."""
    factor = FactorRequestV2.model_validate(run.canonical_request_record["factor_request"])
    warmup_stamp = chunk.evaluation_timestamps[0]
    cursor = _PITCursor(run, chunk.evaluation_timestamps[-1])
    warmup_rows = cursor.warmup_rows(warmup_stamp)
    warmup_phase = "initialize" if chunk.index == 0 else "read_only"
    frames = [{
        "type": "init", "protocol_version": V2_PROTOCOL_VERSION,
        "plan_hash": chunk.plan_hash, "source": factor.source,
        "params": factor.params, "symbols": list(run.research_request.symbols),
        "schema_id": factor.worker_input_schema_id,
        "schema_version": factor.worker_input_schema_version,
        "execution_mode": factor.execution_mode,
        "state_schema_id": factor.state_schema_id,
        "state_schema_version": factor.state_schema_version,
        "state": None,
    }, {
        "type": "warmup", "phase": warmup_phase,
        "as_of": _iso(warmup_stamp), "history": warmup_rows,
    }]
    encoded_size = sum(len(_canonical_bytes(frame)) + 1 for frame in frames)
    if encoded_size > V2_LIMITS.max_input_bytes_per_chunk:
        raise ValueError("serialized chunk input exceeds byte limit")
    for timestamp in chunk.evaluation_timestamps:
        history = cursor.history_rows(timestamp)
        step_frame = {"type": "step", "as_of": _iso(timestamp), "history": history}
        encoded_size += len(_canonical_bytes(step_frame)) + 1
        if encoded_size > V2_LIMITS.max_input_bytes_per_chunk:
            raise ValueError("serialized chunk input exceeds byte limit")
        frames.append(step_frame)
    frames.append({"type": "end"})
    return frames


def plan_chunks(run: AuthorizedFactorRunV2, *, max_steps_per_chunk: int | None = None) -> ChunkPlanV2:
    """Create a stable non-overlapping plan and preflight aggregate input bytes."""
    max_steps = max_steps_per_chunk or V2_LIMITS.max_steps_per_chunk
    if type(max_steps) is not int or not 1 <= max_steps <= V2_LIMITS.max_steps_per_chunk:
        raise ValueError("chunk size must be between 1 and the service per-container limit")
    timestamps = tuple(pd.Timestamp(value).tz_convert("UTC") for value in run.evaluation_timestamps)
    if len(timestamps) > V2_LIMITS.max_total_steps:
        raise ValueError("total evaluation steps exceed service limit")
    if not timestamps:
        raise ValueError("at least one evaluation timestamp is required")
    if len(set(timestamps)) != len(timestamps) or timestamps != tuple(sorted(timestamps)):
        raise ValueError("evaluation timestamps must be unique and ordered")
    base_payload = {
        "identity_hash": run.identity_hash,
        "limits_version": V2_LIMITS.version,
        "chunk_size": max_steps,
        "evaluation_timestamps": [_iso(value) for value in timestamps],
    }
    plan_hash = hashlib.sha256(_canonical_bytes(base_payload)).hexdigest()
    raw_chunks = []
    for index, offset in enumerate(range(0, len(timestamps), max_steps)):
        selected = validate_v2_chunk_timeline(
            timestamps[offset:offset + max_steps],
            start=run.research_request.start, end=run.research_request.end, max_steps=max_steps,
        )
        warmup_timestamps = tuple(
            timestamps[max(0, offset - run.research_request.lookback_bars):offset]
        )
        chunk_hash = hashlib.sha256(_canonical_bytes({
            "plan_hash": plan_hash, "index": index,
            "evaluation_timestamps": [_iso(value) for value in selected],
        })).hexdigest()
        chunk_id = f"{index:08d}-{chunk_hash[:16]}"
        raw_chunks.append(ChunkV2(chunk_id, index, plan_hash, selected, warmup_timestamps))
    chunks = []
    total_input_bytes = 0
    for chunk in raw_chunks:
        frames = build_chunk_frames(run, chunk)
        size = sum(len(_canonical_bytes(frame)) + 1 for frame in frames)
        total_input_bytes += size
        if total_input_bytes > V2_LIMITS.max_total_input_bytes:
            raise ValueError("cumulative chunk input exceeds service byte limit")
        input_hash = hashlib.sha256(_canonical_bytes(frames)).hexdigest()
        chunks.append(replace(chunk, input_bytes=size, input_hash=input_hash))
    return ChunkPlanV2(run.identity_hash, plan_hash, tuple(chunks), len(timestamps), total_input_bytes, V2_LIMITS.version)


def validate_chunk_output(run: AuthorizedFactorRunV2, chunk: ChunkV2, frames: list[dict], output: list[dict]) -> list[dict]:
    """Validate ready marker and exact ordered result set for one committed chunk."""
    if len(frames) != len(chunk.evaluation_timestamps) + 3:
        raise ValueError("chunk request frame count does not match evaluation plan")
    stateful = run.canonical_request_record["factor_request"]["execution_mode"] == "stateful"
    expected_output_count = len(chunk.evaluation_timestamps) + 1 + int(stateful)
    if not isinstance(output, list) or len(output) != expected_output_count:
        raise ValueError("Worker output count does not match chunk plan")
    ready, *tail = output
    checkpoint = tail.pop() if stateful else None
    results = tail
    if ready != {"type": "ready", "protocol_version": V2_PROTOCOL_VERSION}:
        raise ValueError("Worker ready marker violates v2 protocol")
    symbols = set(run.research_request.symbols)
    for expected, item in zip(chunk.evaluation_timestamps, results, strict=True):
        if not isinstance(item, dict) or set(item) != {"type", "plan_hash", "as_of", "values"}:
            raise ValueError("Worker result frame has unexpected fields")
        if item["type"] != "result" or item["plan_hash"] != chunk.plan_hash:
            raise ValueError("Worker result does not match chunk plan")
        try:
            actual = pd.Timestamp(item["as_of"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Worker result timestamp is invalid") from exc
        if actual.tzinfo is None or actual.tz_convert("UTC") != expected:
            raise ValueError("Worker result timestamps are missing, reordered, or duplicated")
        values = item["values"]
        if not isinstance(values, dict) or set(values) != symbols:
            raise ValueError("Worker result symbols do not match authorized symbols")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
               for value in values.values()):
            raise ValueError("Worker factor values must be finite numbers")
    if stateful:
        from alpha_lab_service.worker_protocol_v2 import validate_state_payload

        if (not isinstance(checkpoint, dict) or set(checkpoint) != {"type", "plan_hash", "state"}
                or checkpoint.get("type") != "checkpoint" or checkpoint.get("plan_hash") != chunk.plan_hash):
            raise ValueError("Worker checkpoint is missing or mismatched")
        factor = FactorRequestV2.model_validate(run.canonical_request_record["factor_request"])
        validate_state_payload(checkpoint["state"], factor.state_schema_id, factor.state_schema_version)
    if len(_canonical_bytes(output)) > V2_LIMITS.max_output_bytes_per_chunk:
        raise ValueError("serialized chunk output exceeds byte limit")
    return results
