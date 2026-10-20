"""Append-only manifests and metadata queries for Data Catalog v3."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from functools import lru_cache
from importlib.resources import files
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
import pandas as pd


_SCHEMA_NAME = "dataset-manifest-v3.schema.json"
_IDENTITY_FIELDS = (
    "exchange",
    "market_type",
    "asset",
    "exchange_symbol",
    "data_kind",
    "timeframe",
)


@lru_cache(maxsize=1)
def _manifest_validator() -> Draft202012Validator:
    schema = json.loads(
        files("data_engineering").joinpath("schemas", _SCHEMA_NAME).read_text(
            encoding="utf-8"
        )
    )
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _parse_utc(value: str, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must have an explicit timezone")
    return parsed


def _validate_source_path(source_path: str, *, partition_index: int) -> None:
    path = PurePosixPath(source_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"partition interval {partition_index} has unsafe source_path")
    if "\\" in source_path or "\x00" in source_path:
        raise ValueError(f"partition interval {partition_index} has unsafe source_path")


def validate_v3_manifest(document: dict[str, Any]) -> None:
    """Validate a v3 document and its cross-field temporal/path invariants."""
    if not isinstance(document, dict):
        raise ValueError("catalog v3 manifest must be an object")
    errors = sorted(_manifest_validator().iter_errors(document), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"catalog v3 manifest schema invalid at {location}: {first.message}")

    previous_end: datetime | None = None
    for index, partition in enumerate(document["partitions"]):
        start = _parse_utc(partition["start"], name=f"partition {index} start")
        end = _parse_utc(partition["end"], name=f"partition {index} end")
        first = _parse_utc(
            partition["first_observation"], name=f"partition {index} first_observation"
        )
        last = _parse_utc(
            partition["last_observation"], name=f"partition {index} last_observation"
        )
        if start >= end or first > last or first < start or last >= end:
            raise ValueError(f"partition interval {index} is empty, reversed, or inconsistent")
        if previous_end is not None and start < previous_end:
            raise ValueError(f"partition interval {index} overlaps or is out of order")
        _validate_source_path(partition["source_path"], partition_index=index)
        previous_end = end

    event_cadence = document.get("event_cadence")
    if event_cadence is not None and document["data_kind"] not in {"funding_rate", "open_interest"}:
        raise ValueError("event_cadence is only valid for event datasets")
    if event_cadence is not None:
        from .freqtrade_feather import _parse_timeframe

        _parse_timeframe(event_cadence)

    registered_at = _parse_utc(document["registered_at"], name="registered_at")
    del registered_at


def manifest_fingerprint(document: dict[str, Any]) -> str:
    """Hash canonical manifest content, excluding the registration timestamp."""
    canonical_document = dict(document)
    canonical_document.pop("registered_at", None)
    encoded = json.dumps(
        canonical_document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_symlink_components(path: Path, *, description: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{description} path symlink forbidden")


def _dataset_dir(root: Path, dataset_id: str) -> Path:
    if not isinstance(dataset_id, str) or not dataset_id or Path(dataset_id).name != dataset_id:
        raise ValueError("invalid dataset ID")
    directory = Path(root) / "catalog-v3" / "datasets" / dataset_id
    _reject_symlink_components(directory, description="catalog")
    return directory


def _version_files(root: Path, dataset_id: str) -> list[Path]:
    directory = _dataset_dir(root, dataset_id)
    if directory.is_symlink():
        raise ValueError("catalog dataset directory symlink forbidden")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("catalog dataset path is not a directory")
    files_found = sorted(directory.glob("*.json"))
    if any(path.is_symlink() for path in files_found):
        raise ValueError("catalog manifest symlink forbidden")
    return files_found


def _read_version(path: Path, dataset_id: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_v3_manifest(document)
    if document["dataset_id"] != dataset_id:
        raise ValueError("manifest dataset identity mismatch")
    fingerprint = manifest_fingerprint(document)
    if path.stem != fingerprint:
        raise ValueError("manifest fingerprint mismatch")
    return document


def _atomic_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _record(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest": document,
        "manifest_sha256": manifest_fingerprint(document),
        "manifest_path": path.as_posix(),
    }


def register_dataset(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Append a validated immutable v3 manifest version to the local catalog."""
    validate_v3_manifest(manifest)
    stored = json.loads(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
    fingerprint = manifest_fingerprint(stored)
    directory = _dataset_dir(Path(root), stored["dataset_id"])
    destination = directory / f"{fingerprint}.json"

    existing_files = _version_files(Path(root), stored["dataset_id"])
    versions = [(_read_version(path, stored["dataset_id"]), path) for path in existing_files]
    for document, path in versions:
        if manifest_fingerprint(document) == fingerprint:
            return _record(path, document)

    if versions:
        identities = {
            tuple(document[field] for field in _IDENTITY_FIELDS)
            for document, _ in versions
        }
        incoming_identity = tuple(stored[field] for field in _IDENTITY_FIELDS)
        if identities != {incoming_identity}:
            raise ValueError("dataset identity cannot change across revisions")
        parent_hashes = {
            document["revision"]["previous_manifest_sha256"]
            for document, _ in versions
            if document["revision"]["previous_manifest_sha256"] is not None
        }
        leaves = {
            manifest_fingerprint(document)
            for document, _ in versions
            if manifest_fingerprint(document) not in parent_hashes
        }
        previous = stored["revision"]["previous_manifest_sha256"]
        if len(leaves) != 1 or previous not in leaves:
            raise ValueError("revision does not reference latest manifest")
    elif stored["revision"]["previous_manifest_sha256"] is not None:
        raise ValueError("revision does not reference latest manifest")

    payload = (json.dumps(stored, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    try:
        _atomic_create(destination, payload)
    except FileExistsError:
        # A concurrent writer published the same immutable version.
        return _record(destination, _read_version(destination, stored["dataset_id"]))
    return _record(destination, stored)


def list_datasets(
    root: Path,
    *,
    exchange: str | None = None,
    market_type: str | None = None,
    asset: str | None = None,
    data_kind: str | None = None,
    timeframe: str | None = None,
) -> list[dict[str, Any]]:
    """Return registered manifest versions in stable dataset/version order."""
    base = Path(root) / "catalog-v3" / "datasets"
    _reject_symlink_components(base, description="catalog")
    if not base.exists():
        return []
    records = []
    for directory in sorted(base.iterdir(), key=lambda item: item.name):
        if directory.is_symlink():
            raise ValueError("catalog dataset directory symlink forbidden")
        if not directory.is_dir():
            continue
        for path in _version_files(Path(root), directory.name):
            document = _read_version(path, directory.name)
            if exchange is not None and document["exchange"] != exchange:
                continue
            if market_type is not None and document["market_type"] != market_type:
                continue
            if asset is not None and document["asset"] != asset:
                continue
            if data_kind is not None and document["data_kind"] != data_kind:
                continue
            if timeframe is not None and document["timeframe"] != timeframe:
                continue
            records.append(_record(path, document))
    records.sort(
        key=lambda item: (
            item["manifest"]["dataset_id"],
            item["manifest"]["registered_at"],
            item["manifest_sha256"],
        )
    )
    return records


def _validate_dataset_snapshot(
    root: Path, dataset_id: str, *, source_roots: Mapping[str, Path]
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], pd.DataFrame]], dict[str, Any] | None]:
    """Validate one dataset and return only the verified partition slices."""
    try:
        paths = _version_files(Path(root), dataset_id)
        records = [_record(path, _read_version(path, dataset_id)) for path in paths]
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return ({"dataset_id": dataset_id, "manifest": None, "status": "INVALID",
                 "issues": [{"code": "MANIFEST_INVALID", "message": str(exc)}]}, [], None)
    if not records:
        return ({"dataset_id": dataset_id, "manifest": None, "status": "MISSING", "issues": []}, [], None)
    referenced = {
        record["manifest"]["revision"]["previous_manifest_sha256"]
        for record in records
        if record["manifest"]["revision"]["previous_manifest_sha256"] is not None
    }
    leaves = [record for record in records if record["manifest_sha256"] not in referenced]
    if len(leaves) != 1:
        return ({"dataset_id": dataset_id, "manifest": None, "status": "INVALID",
                 "issues": [{"code": "INVALID_LINEAGE", "message": "catalog has no unique latest version"}]}, [], None)
    latest = leaves[0]
    manifest = latest["manifest"]
    issues: list[dict[str, str]] = []
    registered_quality = manifest.get("quality", {})
    if registered_quality.get("status") == "INVALID":
        issues.extend(registered_quality.get("issues", []))

    def issue(code: str, message: str) -> None:
        issues.append({"code": code, "message": message})

    from .freqtrade_feather import (
        _canonicalize_frame, _frame_fingerprint, _safe_source_path, _source_digest,
    )
    from .quality.contracts import validate_observations

    verified_partitions: list[tuple[dict[str, Any], pd.DataFrame]] = []
    source_cache: dict[tuple[str, str], tuple[Path, pd.DataFrame, str]] = {}
    for index, partition in enumerate(manifest["partitions"]):
        root_id = partition["source_root_id"]
        configured_root = source_roots.get(root_id)
        if configured_root is None:
            issue("SOURCE_ROOT_MISSING", f"no source root configured for {root_id}")
            continue
        try:
            key = (root_id, partition["source_path"])
            if key not in source_cache:
                source_path, _ = _safe_source_path(configured_root, Path(partition["source_path"]))
                source_frame = pd.read_feather(source_path)
                source_frame = _canonicalize_frame(source_frame, manifest["data_kind"])
                source_cache[key] = (source_path, source_frame, _source_digest(source_path))
            source_path, frame, source_hash = source_cache[key]
            if source_hash != partition["source_sha256"]:
                issue("SOURCE_HASH_MISMATCH", f"partition {index} source bytes changed")
                continue
            dates = pd.to_datetime(frame["date"], utc=True)
            start = pd.Timestamp(partition["start"])
            end = pd.Timestamp(partition["end"])
            part = frame.loc[(dates >= start) & (dates < end)].copy()
            if len(part) != partition["row_count"]:
                issue("PARTITION_ROW_COUNT_MISMATCH", f"partition {index} row count changed")
            if _frame_fingerprint(part) != partition["fingerprint"]:
                issue("PARTITION_FINGERPRINT_MISMATCH", f"partition {index} observations changed")
            if not part.empty:
                verified_partitions.append((partition, part))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issue("SOURCE_INVALID", f"partition {index}: {exc}")

    source_order_issues: list[dict[str, str]] = []
    for source_key, (_, source_frame, _) in source_cache.items():
        if "date" not in source_frame:
            continue
        source_partitions = [
            part for part in manifest["partitions"]
            if (part["source_root_id"], part["source_path"]) == source_key
        ]
        if not source_partitions:
            continue
        dates = pd.to_datetime(source_frame["date"], utc=True)
        selected_mask = pd.Series(False, index=source_frame.index)
        for part in source_partitions:
            selected_mask |= (dates >= pd.Timestamp(part["start"])) & (dates < pd.Timestamp(part["end"]))
        selected_source = source_frame.loc[selected_mask]
        original_quality = validate_observations(
            selected_source, data_kind=manifest["data_kind"], timeframe=manifest["timeframe"],
            event_cadence=manifest.get("event_cadence"),
        )
        source_order_issues.extend(original_quality["issues"])

    if verified_partitions:
        frame = pd.concat([part for _, part in verified_partitions], ignore_index=True)
        quality = validate_observations(
            frame, data_kind=manifest["data_kind"], timeframe=manifest["timeframe"],
            event_cadence=manifest.get("event_cadence"),
        )
        issues.extend(quality["issues"])
        for quality_issue in source_order_issues:
            if quality_issue not in issues:
                issues.append(quality_issue)
        status = "INVALID" if issues or quality["status"] == "INVALID" else quality["status"]
    else:
        quality = {"row_count": 0, "observed_interval": None, "cadence": {},
                   "status": "MISSING", "issues": []}
        status = "INVALID" if issues else "MISSING"
    result = {"dataset_id": dataset_id, "manifest": manifest, "status": status,
              "quality": quality, "issues": issues}
    return result, verified_partitions, latest


def validate_dataset(
    root: Path, dataset_id: str, *, source_roots: Mapping[str, Path]
) -> dict[str, Any]:
    """Verify the latest registered version against its configured source bytes."""
    result, _, _ = _validate_dataset_snapshot(root, dataset_id, source_roots=source_roots)
    return result


def get_coverage(
    root: Path,
    dataset_id: str,
    start: datetime,
    end: datetime,
    *,
    source_roots: Mapping[str, Path],
):
    """Re-export the interval query alongside the catalog APIs."""
    from .dataset_coverage import get_coverage as query_coverage

    return query_coverage(root, dataset_id, start, end, source_roots=source_roots)
