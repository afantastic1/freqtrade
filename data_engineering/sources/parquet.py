"""Immutable Parquet object storage and append-only Catalog v3 registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Mapping

import pandas as pd

from ..catalog_v3 import (
    _reject_symlink_components,
    list_datasets,
    manifest_fingerprint,
    register_dataset,
    validate_dataset,
)
from ..freqtrade_feather import _frame_fingerprint
from .contracts import ObservationBatch, ValidationReceipt
from .pipeline import (
    ValidationPolicy,
    encode_parquet,
    normalized_observations,
    validate,
)


class PublishStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    ALREADY_PRESENT = "ALREADY_PRESENT"


@dataclass(frozen=True, slots=True)
class PublishResult:
    status: PublishStatus
    object_path: Path
    partition: dict
    manifest: dict


def provenance_key(
    receipt: ValidationReceipt, adapter_name: str, source_locator: str
) -> str:
    """Return a stable lineage key independent of volatile retrieval metadata."""
    material = {
        "adapter": adapter_name,
        "raw_source_sha256": receipt.source_sha256,
        "converter_version": receipt.converter_version,
        "schema_version": receipt.schema_version,
        "source_locator": source_locator,
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _iso(value: datetime | pd.Timestamp) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("partition timestamps must be timezone-aware")
    return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _atomic_create(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise ValueError("immutable Parquet path symlink forbidden")
    fd, temp_name = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise ValueError("immutable Parquet object conflicts with existing bytes")
    finally:
        temp.unlink(missing_ok=True)


def _write_object(root: Path, relative_path: Path, payload: bytes) -> Path:
    root = _reject_symlink_components(Path(root), description="normalized root")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / relative_path
    _reject_symlink_components(destination.parent, description="normalized partition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination, description="normalized partition")
    _atomic_create(destination, payload)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest != hashlib.sha256(payload).hexdigest():
        raise ValueError("published Parquet hash differs from validated bytes")
    return destination


def _verify_receipt(receipt: ValidationReceipt, batch: ObservationBatch) -> tuple[pd.DataFrame, bytes]:
    if receipt.dataset_id != batch.identity.dataset_id() or receipt.request_id != batch.artifact.request_id:
        raise ValueError("validation receipt does not match dataset or request identity")
    if (receipt.start, receipt.end) != (batch.artifact.start, batch.artifact.end):
        raise ValueError("validation receipt interval does not match source artifact")
    if receipt.schema_version != batch.identity.schema_version:
        raise ValueError("validation receipt schema does not match dataset identity")
    rechecked = validate(
        batch,
        converter_version=receipt.converter_version,
        policy=ValidationPolicy(allow_partial=receipt.quality_status == "PARTIAL"),
    )
    if rechecked != receipt:
        raise ValueError("validation receipt digests or metadata do not match source bytes")
    frame = normalized_observations(batch)
    payload = encode_parquet(frame)
    if hashlib.sha256(payload).hexdigest() != receipt.normalized_sha256:
        raise ValueError("validation receipt Parquet digest mismatch")
    if _frame_fingerprint(frame) != receipt.content_fingerprint:
        raise ValueError("validation receipt content fingerprint mismatch")
    return frame, payload


def _overlaps(left: dict, right_start: datetime, right_end: datetime) -> bool:
    return _iso(left["start"]) < _iso(right_end) and _iso(left["end"]) > _iso(right_start)


def publish(
    receipt: ValidationReceipt,
    batch: ObservationBatch,
    roots: Mapping[str, Path],
    catalog: Path,
    *,
    normalized_root_id: str = "alpha-lab-normalized",
) -> PublishResult:
    """Verify a validation receipt, atomically write Parquet, then append Catalog v3."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", normalized_root_id):
        raise ValueError("invalid normalized root ID")
    normalized_root = roots.get(normalized_root_id)
    if normalized_root is None:
        raise ValueError("normalized root must be configured in roots")
    frame, payload = _verify_receipt(receipt, batch)
    raw_digest = hashlib.sha256(payload).hexdigest()
    adapter_name = batch.metadata.get("adapter") or batch.artifact.metadata.get("adapter")
    source_locator = batch.metadata.get("source_locator") or batch.artifact.source_locator
    if not adapter_name or not source_locator:
        raise ValueError("adapter and source locator provenance are required")
    lineage_key = provenance_key(receipt, adapter_name, source_locator)
    month = batch.artifact.start.strftime("%m")
    year = batch.artifact.start.strftime("%Y")
    relative_path = Path(receipt.dataset_id) / f"year={year}" / f"month={month}" / (
        f"part-{receipt.content_fingerprint}-{lineage_key}.parquet"
    )
    object_path = Path(normalized_root) / relative_path
    source_relative = PurePosixPath(*relative_path.parts).as_posix()
    storage_format = "parquet"
    partition = {
        "start": _iso(batch.artifact.start),
        "end": _iso(batch.artifact.end),
        "source_root_id": normalized_root_id,
        "source_path": source_relative,
        "first_observation": _iso(frame["date"].iloc[0]),
        "last_observation": _iso(frame["date"].iloc[-1]),
        "row_count": len(frame),
        "source_sha256": raw_digest,
        "fingerprint": receipt.content_fingerprint,
        "quality_status": receipt.quality_status,
        "storage_format": storage_format,
        "provenance": {
            "adapter": adapter_name,
            "converter_version": receipt.converter_version,
            "schema_version": receipt.schema_version,
            "raw_source_sha256": receipt.source_sha256,
            "source_locator": source_locator,
            "artifact_start": _iso(batch.artifact.start),
            "artifact_end": _iso(batch.artifact.end),
        },
    }
    retrieved_at = batch.metadata.get("retrieved_at_utc")
    if retrieved_at:
        partition["provenance"]["retrieved_at_utc"] = _iso(pd.Timestamp(retrieved_at))

    validated = validate_dataset(
        Path(catalog), receipt.dataset_id, source_roots=dict(roots)
    )
    if validated["status"] == "INVALID":
        raise ValueError("latest Catalog v3 revision is invalid; refusing publication")
    previous = validated.get("manifest")
    previous_partitions = list(previous.get("partitions", [])) if previous else []
    carried: list[dict] = []
    for existing in previous_partitions:
        if not _overlaps(existing, batch.artifact.start, batch.artifact.end):
            carried.append(existing)
            continue
        same_bounds = (
            _iso(existing["start"]) == _iso(batch.artifact.start)
            and _iso(existing["end"]) == _iso(batch.artifact.end)
        )
        if not same_bounds:
            raise ValueError("new partition overlaps an existing partition interval")
        if existing.get("storage_format", "feather") == "parquet":
            existing_provenance = existing.get("provenance", {})
            if (
                existing["source_path"] == source_relative
                and existing["source_sha256"] == raw_digest
                and existing_provenance.get("raw_source_sha256") == receipt.source_sha256
                and existing_provenance.get("converter_version") == receipt.converter_version
                and existing_provenance.get("adapter") == adapter_name
                and existing_provenance.get("source_locator") == source_locator
            ):
                latest_hash = manifest_fingerprint(validated["manifest"])
                latest_record = next(
                    record for record in list_datasets(Path(catalog))
                    if record["manifest_sha256"] == latest_hash
                )
                return PublishResult(PublishStatus.ALREADY_PRESENT, object_path, existing, latest_record)
            raise ValueError("Parquet partition already exists for this interval with different provenance")
        # Exact-bound Feather-to-Parquet normalization creates a new immutable
        # manifest revision; older manifest versions keep the Feather reference.

    object_path = _write_object(Path(normalized_root), relative_path, payload)
    active_partitions = sorted([*carried, partition], key=lambda item: item["start"])
    all_statuses = [item["quality_status"] for item in active_partitions]
    overall_status = (
        "INVALID" if "INVALID" in all_statuses else
        "PARTIAL" if "PARTIAL" in all_statuses else
        "AVAILABLE"
    )
    previous_hash = manifest_fingerprint(previous) if previous else None
    manifest = {
        "schema_version": "data-catalog-v3",
        "dataset_id": receipt.dataset_id,
        "exchange": batch.identity.exchange,
        "market_type": batch.identity.market_type,
        "asset": batch.identity.asset,
        "exchange_symbol": batch.identity.exchange_symbol,
        "data_kind": batch.identity.data_kind,
        "timeframe": batch.identity.timeframe,
        "source": adapter_name,
        "source_reference": source_locator,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "quality": {"status": overall_status, "issues": [], "summary": {"partition_count": len(active_partitions)}},
        "partitions": active_partitions,
        "revision": {
            "previous_manifest_sha256": previous_hash,
            "reason": "publish immutable normalized Parquet partition",
        },
    }
    record = register_dataset(Path(catalog), manifest)
    verified_after_publish = validate_dataset(
        Path(catalog), receipt.dataset_id, source_roots=dict(roots)
    )
    if verified_after_publish["status"] == "INVALID":
        raise RuntimeError("registered Parquet snapshot failed Catalog v3 verification")
    return PublishResult(PublishStatus.PUBLISHED, object_path, partition, record)
