"""Read-only planning and source validation before immutable publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

import pandas as pd

from ..catalog_v3 import validate_dataset
from ..data_kind_registry import DATA_KIND_CONTRACTS
from ..freqtrade_feather import _frame_fingerprint, _parse_timeframe
from ..quality.contracts import validate_observations
from .contracts import (
    AvailabilityStatus,
    DatasetIdentity,
    DatasetRequest,
    ObservationBatch,
    SourceArtifact,
    ValidationReceipt,
)
from .journal import JournalState, JournalStore


class PlanStatus(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    PLANNED = "PLANNED"


@dataclass(frozen=True, slots=True)
class MonthPlan:
    month: str
    start: datetime
    end: datetime
    status: PlanStatus
    artifact: SourceArtifact | None = None
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourcePlan:
    dataset_id: str
    request_id: str
    months: tuple[MonthPlan, ...]
    dry_run: bool = True


class SourceAdapter(Protocol):
    def discover(self, request: DatasetRequest) -> list[SourceArtifact]: ...


class CatalogView(Protocol):
    def inspect(self, identity: DatasetIdentity) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Explicit allowances for publishing incomplete local source intervals."""

    allow_partial: bool = False


class CatalogV3View:
    """Verify the latest Catalog v3 snapshot before using its coverage."""

    def __init__(self, root: Path, *, source_roots: Mapping[str, Path]) -> None:
        self.root = Path(root)
        self.source_roots = dict(source_roots)

    def inspect(self, identity: DatasetIdentity) -> Mapping[str, Any]:
        return validate_dataset(self.root, identity.dataset_id(), source_roots=self.source_roots)


def _month_bounds(start: datetime, end: datetime):
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        next_month = cursor.replace(
            year=cursor.year + (cursor.month == 12),
            month=1 if cursor.month == 12 else cursor.month + 1,
        )
        yield cursor.strftime("%Y-%m"), max(start, cursor), min(end, next_month)
        cursor = next_month


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("catalog partition bounds must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _partition_covers(
    partitions: list[Mapping[str, Any]], start: datetime, end: datetime, data_kind: str
) -> bool:
    cursor = start
    selected = sorted(
        (part for part in partitions if part.get("storage_format", "feather") == "parquet"),
        key=lambda part: part.get("start", ""),
    )
    for partition in selected:
        if partition.get("quality_status") == "INVALID":
            continue
        if data_kind in {"ohlcv", "mark_price"} and partition.get("quality_status") != "AVAILABLE":
            continue
        part_start = _parse_time(partition["start"])
        part_end = _parse_time(partition["end"])
        if part_end <= cursor or part_start > cursor:
            continue
        if data_kind in {"funding_rate", "open_interest"}:
            provenance = partition.get("provenance", {})
            if provenance.get("artifact_start") != partition["start"] or provenance.get("artifact_end") != partition["end"]:
                continue
        cursor = max(cursor, part_end)
        if cursor >= end:
            return True
    return cursor >= end


def plan(
    request: DatasetRequest,
    adapter: SourceAdapter,
    catalog: CatalogView,
    *,
    dry_run: bool = True,
) -> SourcePlan:
    """Compare source availability with verified normalized Parquet coverage."""
    discovered = adapter.discover(request)
    artifacts_by_month: dict[str, list[SourceArtifact]] = {}
    for artifact in discovered:
        month = artifact.start.strftime("%Y-%m")
        artifacts_by_month.setdefault(month, []).append(artifact)
    snapshot = catalog.inspect(request.identity)
    snapshot_status = snapshot.get("status", "MISSING")
    manifest = snapshot.get("manifest") or {}
    partitions = manifest.get("partitions", [])
    months: list[MonthPlan] = []
    for month, start, end in _month_bounds(request.start, request.end):
        candidates = artifacts_by_month.get(month, [])
        artifact = candidates[0] if len(candidates) == 1 else None
        if snapshot_status == "INVALID":
            status = PlanStatus.INVALID
            issues = tuple(
                item.get("code", "CATALOG_INVALID")
                for item in snapshot.get("issues", []) if isinstance(item, Mapping)
            ) or ("CATALOG_INVALID",)
        elif _partition_covers(partitions, start, end, request.identity.data_kind):
            status, issues = PlanStatus.PRESENT, ()
        elif len(candidates) > 1:
            status, issues = PlanStatus.INVALID, ("AMBIGUOUS_SOURCE_ARTIFACTS",)
        elif artifact is None:
            status, issues = PlanStatus.MISSING, ()
        elif artifact.availability is AvailabilityStatus.UNAVAILABLE:
            status, issues = PlanStatus.UNAVAILABLE, ()
        else:
            status, issues = PlanStatus.PLANNED, ()
        months.append(MonthPlan(month, start, end, status, artifact, issues))
    return SourcePlan(request.identity.dataset_id(), request.request_id, tuple(months), dry_run)


class SourceValidationError(ValueError):
    """Source data failed the normalized observation contract."""

    def __init__(self, issue_codes: tuple[str, ...], report: Mapping[str, Any]):
        self.issue_codes = issue_codes
        self.report = dict(report)
        super().__init__("source validation failed: " + ", ".join(issue_codes))


def _canonical_columns(data_kind: str, columns) -> list[str]:
    contract = DATA_KIND_CONTRACTS.get(data_kind)
    if contract is None:
        raise ValueError(f"unsupported data kind: {data_kind}")
    timestamp = contract["timestamp_field"]
    if data_kind == "ohlcv":
        required = [timestamp, "open", "high", "low", "close", "volume"]
    elif data_kind == "mark_price":
        required = [timestamp, "mark_price"]
    elif data_kind == "funding_rate":
        required = [timestamp, "funding_rate"]
    else:
        required = [timestamp, *sorted(contract["one_of_fields"] & set(columns))]
    missing = set(contract["required_fields"]) - set(columns)
    if missing or (contract["one_of_fields"] and not (contract["one_of_fields"] & set(columns))):
        raise SourceValidationError(("MISSING_REQUIRED_FIELD",), {"missing": sorted(missing)})
    return required


def _actual_source_sha(batch: ObservationBatch) -> str:
    path = batch.artifact.local_path
    if path is None:
        return batch.source_sha256
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_parquet(frame: pd.DataFrame) -> bytes:
    output = BytesIO()
    frame.to_parquet(output, engine="pyarrow", index=False, compression="snappy", version="2.6")
    return output.getvalue()


def normalized_observations(batch: ObservationBatch) -> pd.DataFrame:
    """Select canonical columns and normalize UTC timestamp representation."""
    if not isinstance(batch.observations, pd.DataFrame):
        raise SourceValidationError(("UNSUPPORTED_OBSERVATIONS",), {})
    fields = _canonical_columns(batch.identity.data_kind, batch.observations.columns)
    frame = batch.observations.loc[:, fields].copy()
    timestamp_field = DATA_KIND_CONTRACTS[batch.identity.data_kind]["timestamp_field"]
    try:
        timestamps = pd.to_datetime(frame[timestamp_field], errors="raise")
    except (TypeError, ValueError) as exc:
        raise SourceValidationError(("INVALID_TIMESTAMP",), {"detail": str(exc)}) from exc
    if timestamps.dt.tz is None:
        raise SourceValidationError(("NAIVE_TIMESTAMP",), {})
    offsets = timestamps.map(lambda item: item.utcoffset().total_seconds())
    if (offsets != 0).any():
        raise SourceValidationError(("NON_UTC_TIMESTAMP",), {})
    frame[timestamp_field] = timestamps.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    if frame.empty:
        raise SourceValidationError(("EMPTY_SOURCE",), {})
    if ((frame[timestamp_field] < batch.artifact.start) |
            (frame[timestamp_field] >= batch.artifact.end)).any():
        raise SourceValidationError(("OBSERVATION_OUT_OF_BOUNDS",), {})
    return frame


def validate(
    batch: ObservationBatch,
    *,
    converter_version: str,
    policy: ValidationPolicy | None = None,
) -> ValidationReceipt:
    """Validate ordered source observations and bind their Parquet bytes by digest."""
    policy = policy or ValidationPolicy()
    source_digest = _actual_source_sha(batch)
    if source_digest != batch.source_sha256:
        raise SourceValidationError(("SOURCE_CHANGED",), {"source_sha256": source_digest})
    frame = normalized_observations(batch)
    timestamp_field = DATA_KIND_CONTRACTS[batch.identity.data_kind]["timestamp_field"]
    timestamps = frame[timestamp_field]
    quality = validate_observations(
        frame,
        data_kind=batch.identity.data_kind,
        timeframe=batch.identity.timeframe,
        event_cadence=batch.artifact.metadata.get("event_cadence"),
    )
    issue_codes = [item["code"] for item in quality["issues"]]
    status = quality["status"]
    if quality["cadence"]["missing_observations"]:
        issue_codes.append("MISSING_CANDLES")
        status = "INVALID"
    if batch.identity.data_kind in {"ohlcv", "mark_price"}:
        step = _parse_timeframe(batch.identity.timeframe)
        duration = (batch.artifact.end - batch.artifact.start).total_seconds()
        stamps = pd.DatetimeIndex(timestamps)
        full_interval = (
            duration % step == 0
            and stamps.min().to_pydatetime() == batch.artifact.start
            and stamps.max().to_pydatetime() == batch.artifact.end - pd.Timedelta(seconds=step)
            and len(stamps) == int(duration // step)
        )
        if not full_interval:
            if batch.artifact.source_format == "vision-monthly-zip-csv":
                issue_codes.append("INCOMPLETE_MONTH")
                status = "INVALID"
            elif status != "INVALID":
                status = "PARTIAL"
    if issue_codes:
        raise SourceValidationError(tuple(dict.fromkeys(issue_codes)), quality)
    if status == "PARTIAL" and not policy.allow_partial:
        raise SourceValidationError(("INCOMPLETE_SOURCE_COVERAGE",), quality)
    payload = encode_parquet(frame)
    normalized_digest = sha256(payload).hexdigest()
    content_fingerprint = _frame_fingerprint(frame)
    receipt_material = {
        "dataset_id": batch.identity.dataset_id(),
        "request_id": batch.artifact.request_id,
        "source_sha256": source_digest,
        "normalized_sha256": normalized_digest,
        "converter_version": converter_version,
        "schema_version": batch.identity.schema_version,
        "start": batch.artifact.start.isoformat(),
        "end": batch.artifact.end.isoformat(),
        "quality_status": status,
        "row_count": len(frame),
        "content_fingerprint": content_fingerprint,
    }
    validation_fingerprint = sha256(
        json.dumps(receipt_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ValidationReceipt(
        dataset_id=batch.identity.dataset_id(),
        request_id=batch.artifact.request_id,
        source_sha256=source_digest,
        normalized_sha256=normalized_digest,
        converter_version=converter_version,
        schema_version=batch.identity.schema_version,
        start=batch.artifact.start,
        end=batch.artifact.end,
        validation_fingerprint=validation_fingerprint,
        quality_status=status,
        issue_codes=(),
        row_count=len(frame),
        content_fingerprint=content_fingerprint,
    )
