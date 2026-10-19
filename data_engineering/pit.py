"""Versioned, content-addressed PIT evidence validation for Catalog snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from importlib.resources import files
import json
from pathlib import Path
from typing import Literal

import pandas as pd
from jsonschema import Draft202012Validator, FormatChecker


PIT_SCHEMA_VERSION = "pit-evidence-v2"
PIT_STATUSES = frozenset({"VERIFIED", "CONSERVATIVE", "UNVERIFIED", "REJECTED"})


@dataclass(frozen=True)
class PITRecord:
    source_asset_id: str
    metric: str
    event_time: pd.Timestamp
    ingested_at: pd.Timestamp
    revision_id: str | None
    status: Literal["VERIFIED", "CONSERVATIVE", "UNVERIFIED", "REJECTED"]
    available_at: pd.Timestamp | None
    available_at_upper_bound: pd.Timestamp | None
    effective_available_at: pd.Timestamp | None
    evidence_sha256s: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True)
class PITAssessment:
    dataset_id: str
    snapshot_sha256: str
    sidecar_sha256: str
    status: str
    ingested_at: pd.Timestamp
    records: tuple[PITRecord, ...]


def _time(value: str, field: str) -> pd.Timestamp:
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if result.tzinfo is None:
        raise ValueError(f"{field} must have a timezone")
    return result.tz_convert("UTC")


def _manifest_ingested_at(manifest: dict) -> pd.Timestamp:
    value = manifest.get("ingested_at", manifest.get("published_at"))
    if value is None:
        raise ValueError("manifest has no actual ingested_at timestamp")
    return _time(value, "manifest ingested_at")


def _validate_schema(document: dict) -> None:
    schemas = {
        "pit-evidence-v1": "pit-evidence-v1.schema.json",
        "pit-evidence-v2": "pit-evidence-v2.schema.json",
    }
    schema_name = schemas.get(document.get("schema_version"))
    if schema_name is None:
        raise ValueError(f"unsupported PIT sidecar version: {document.get('schema_version')}")
    schema = json.loads(
        files("data_engineering").joinpath(
            "schemas", schema_name
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"PIT sidecar schema invalid at {location}: {first.message}")


def _verify_evidence(root: Path, evidence: list[dict]) -> dict[str, dict]:
    evidence_root = root / "pit_evidence"
    if evidence_root.is_symlink():
        raise ValueError("PIT evidence directory symlink is forbidden")
    result = {}
    for item in evidence:
        digest = item["sha256"]
        path = evidence_root / digest
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or symlinked PIT evidence blob: {digest}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"evidence SHA-256 mismatch: {digest}")
        descriptor = item
        try:
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"PIT evidence must be a structured JSON record: {digest}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"PIT evidence must be a JSON object: {digest}")
        result[digest] = {"descriptor": descriptor, "claims": parsed}
    return result


def _same_numeric_value(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return False


def _require_row_evidence(
    evidence_by_hash: dict[str, dict], refs: tuple[str, ...], *,
    manifest: dict, source_row: dict, item: dict, revision_id: str,
    expected_kind: str, effective_time_key: str,
) -> None:
    for digest in refs:
        evidence = evidence_by_hash[digest]
        claim = evidence["claims"]
        if claim is None or evidence["descriptor"]["role"] != "source_record":
            continue
        event_time = _time(source_row["event_time"], "source event_time")
        claim_event_time = claim.get("event_time")
        if claim_event_time is None:
            continue
        if (
            claim.get("kind") == expected_kind
            and claim.get("dataset_id") == manifest["dataset_id"]
            and claim.get("snapshot_sha256") == manifest["sha256"]
            and claim.get("source_asset_id") == str(source_row["asset_id"])
            and claim.get("metric") == str(source_row["metric"])
            and _time(claim_event_time, "evidence event_time") == event_time
            and _same_numeric_value(claim.get("value"), source_row["value"])
            and claim.get("revision_id") == revision_id
            and claim.get(effective_time_key) == item[effective_time_key]
        ):
            return
    raise ValueError(
        f"{expected_kind} evidence does not prove snapshot observation value, revision, and time"
    )


def validate_pit_sidecar(
    root: str | Path,
    manifest: dict,
    source_rows: list[dict],
    *,
    sidecar_bytes: bytes,
    sidecar_sha256: str,
) -> PITAssessment:
    """Validate an immutable sidecar against one manifest and all source rows.

    The caller supplies parsed source rows from the already digest-verified CSV.
    Evidence material is local and content-addressed; no network fetch is made.
    """
    if hashlib.sha256(sidecar_bytes).hexdigest() != sidecar_sha256:
        raise ValueError("sidecar SHA-256 mismatch")
    try:
        document = json.loads(sidecar_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid PIT sidecar JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("PIT sidecar must be a JSON object")
    _validate_schema(document)
    if document.get("schema_version") != PIT_SCHEMA_VERSION:
        raise ValueError(f"unsupported PIT sidecar version: {document.get('schema_version')}")
    if (document["dataset_id"] != manifest.get("dataset_id")
            or document["snapshot_sha256"] != manifest.get("sha256")
            or document["source"] != manifest.get("source")):
        raise ValueError("snapshot identity mismatch")
    ingested_at = _manifest_ingested_at(manifest)
    sidecar_ingested_at = _time(document["ingested_at"], "sidecar ingested_at")
    if sidecar_ingested_at != ingested_at:
        raise ValueError("ingested_at mismatch")

    evidence = document.get("evidence", [])
    evidence_by_hash = _verify_evidence(Path(root), evidence)
    evidence_hashes = set(evidence_by_hash)
    source_by_key = {}
    for row in source_rows:
        event_time = _time(row["event_time"], "source event_time")
        key = (str(row["asset_id"]), str(row["metric"]), event_time)
        if key in source_by_key:
            raise ValueError("duplicate source observation key")
        source_by_key[key] = row

    records_by_key = {}
    records = []
    overall_status = document["status"]
    for item in document["records"]:
        event_time = _time(item["event_time"], "record event_time")
        key = (item["source_asset_id"], item["metric"], event_time)
        if key in records_by_key:
            raise ValueError("duplicate PIT record key")
        if key not in source_by_key:
            if any(k[:2] == key[:2] for k in source_by_key):
                raise ValueError("PIT event_time mismatch")
            raise ValueError("sidecar contains an extra source row")
        refs = tuple(item["evidence_sha256s"])
        if not set(refs) <= evidence_hashes:
            raise ValueError("PIT record references undeclared evidence")
        status = item["status"]
        available_at = (
            _time(item["available_at"], "available_at")
            if "available_at" in item else None
        )
        upper_bound = (
            _time(item["available_at_upper_bound"], "available_at_upper_bound")
            if "available_at_upper_bound" in item else None
        )
        revision_id = item.get("revision_id")
        reason = item.get("reason")
        if status == "VERIFIED":
            if not revision_id:
                raise ValueError("VERIFIED revision_id is required")
            if not refs:
                raise ValueError("VERIFIED records require revision_id and evidence")
            if upper_bound is not None or item["availability_basis"] not in {
                "source_release_record", "archived_source_vintage"
            }:
                raise ValueError("VERIFIED records require exact source availability evidence")
            effective = available_at
            if effective < event_time:
                raise ValueError("availability precedes event_time")
            if effective > ingested_at:
                raise ValueError("availability is after ingested_at")
            _require_row_evidence(
                evidence_by_hash, refs, manifest=manifest,
                source_row=source_by_key[key], item=item, revision_id=revision_id,
                expected_kind=item["availability_basis"], effective_time_key="available_at",
            )
        elif status == "CONSERVATIVE":
            if not revision_id:
                raise ValueError("CONSERVATIVE revision_id is required")
            if not refs:
                raise ValueError("CONSERVATIVE records require revision_id and evidence")
            if available_at is not None or item["availability_basis"] != "audited_upper_bound":
                raise ValueError("CONSERVATIVE records require an audited upper bound")
            effective = upper_bound
            if effective < event_time:
                raise ValueError("availability precedes event_time")
            if effective > ingested_at:
                raise ValueError("availability is after ingested_at")
            _require_row_evidence(
                evidence_by_hash, refs, manifest=manifest,
                source_row=source_by_key[key], item=item, revision_id=revision_id,
                expected_kind="audited_upper_bound",
                effective_time_key="available_at_upper_bound",
            )
        else:
            if available_at is not None or upper_bound is not None:
                raise ValueError(f"{status} records cannot claim availability")
            if status == "UNVERIFIED" and refs:
                raise ValueError("UNVERIFIED records cannot claim PIT evidence")
            effective = None
        records_by_key[key] = item
        records.append(PITRecord(
            source_asset_id=item["source_asset_id"], metric=item["metric"],
            event_time=event_time, ingested_at=ingested_at,
            revision_id=revision_id, status=status,
            available_at=available_at, available_at_upper_bound=upper_bound,
            effective_available_at=effective, evidence_sha256s=refs,
            reason=reason,
        ))
    if set(records_by_key) != set(source_by_key):
        raise ValueError("PIT sidecar does not cover every snapshot row")
    return PITAssessment(
        dataset_id=manifest["dataset_id"], snapshot_sha256=manifest["sha256"],
        sidecar_sha256=sidecar_sha256, status=overall_status,
        ingested_at=ingested_at, records=tuple(records),
    )
