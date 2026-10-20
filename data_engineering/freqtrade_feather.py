"""Read-only discovery and registration of local Freqtrade Feather datasets."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any

import pandas as pd

from .catalog_v3 import list_datasets, register_dataset
from .data_kind_registry import DATA_KIND_CONTRACTS


_SOURCE_ROOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_FILENAME = re.compile(
    r"^(?P<pair>[A-Z0-9_]+)-(?P<timeframe>[1-9][0-9]*(?:s|m|h|d|w))"
    r"(?:-(?P<kind>futures|funding_rate|open_interest|mark))?$",
    re.IGNORECASE,
)
_STEP_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_timeframe(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smhdw])", value)
    if match is None:
        raise ValueError(f"unsupported timeframe: {value}")
    return int(match.group(1)) * _STEP_SECONDS[match.group(2)]


def _iso(value: Any) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must have an explicit UTC timezone")
    stamp = stamp.tz_convert("UTC")
    return stamp.isoformat().replace("+00:00", "Z")


def _safe_root(source_root: Path) -> Path:
    root = Path(source_root)
    if root.is_symlink():
        raise ValueError("source root symlink forbidden")
    if not root.is_dir():
        raise ValueError("source root must be an existing directory")
    return root.resolve(strict=True)


def _safe_source_path(source_root: Path, relative_path: Path) -> tuple[Path, str]:
    root = _safe_root(source_root)
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("source path escapes configured root")
    source = Path(source_root) / relative
    current = Path(source_root)
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("source symlink forbidden")
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("source path escapes configured root") from exc
    if not resolved.is_file():
        raise ValueError("source must be a regular file")
    return resolved, PurePosixPath(*relative.parts).as_posix()


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_filename(
    filename: str, *, exchange: str, market_type: str
) -> dict[str, Any]:
    stem = filename[:-len(".feather")] if filename.lower().endswith(".feather") else filename
    match = _FILENAME.fullmatch(stem)
    if match is None:
        raise ValueError("filename does not match a supported Freqtrade pair/timeframe pattern")
    suffix = (match.group("kind") or "").lower()
    parts = match.group("pair").upper().split("_")
    if market_type == "futures":
        if suffix not in {"futures", "funding_rate", "open_interest", "mark"}:
            raise ValueError("futures source requires a futures or futures-data filename suffix")
        if len(parts) != 3:
            raise ValueError("futures filename must contain base, quote, and settlement assets")
        base, quote, settle = parts
        exchange_symbol = f"{base}/{quote}:{settle}"
        data_kind = {
            "futures": "ohlcv",
            "funding_rate": "funding_rate",
            "open_interest": "open_interest",
            "mark": "mark_price",
        }[suffix]
    elif market_type == "spot":
        if suffix or len(parts) != 2:
            raise ValueError("spot filename must contain base and quote with no futures-data suffix")
        base, quote = parts
        exchange_symbol = f"{base}/{quote}"
        data_kind = "ohlcv"
    else:
        raise ValueError("market_type must be spot or futures")
    if data_kind in {"funding_rate", "open_interest", "mark_price"} and market_type != "futures":
        raise ValueError("event and mark-price datasets require futures market type")
    return {
        "exchange": exchange,
        "market_type": market_type,
        "asset": base,
        "exchange_symbol": exchange_symbol,
        "data_kind": data_kind,
        "timeframe": match.group("timeframe"),
    }


def _validate_columns(frame: pd.DataFrame, data_kind: str) -> None:
    contract = DATA_KIND_CONTRACTS.get(data_kind)
    if contract is None:
        raise ValueError(f"unsupported data kind: {data_kind}")
    columns = set(frame.columns)
    missing = contract["required_fields"] - columns
    if missing:
        raise ValueError(f"missing {data_kind} fields: {sorted(missing)}")
    if contract["one_of_fields"] and not (contract["one_of_fields"] & columns):
        raise ValueError(
            f"{data_kind} requires one of {sorted(contract['one_of_fields'])}"
        )
    if frame.empty:
        raise ValueError("empty dataset")
    if frame[contract["timestamp_field"]].isna().any():
        raise ValueError("timestamp contains null values")
    stamps = pd.to_datetime(frame[contract["timestamp_field"]], errors="coerce")
    if stamps.isna().any() or stamps.dt.tz is None:
        raise ValueError("timestamp must have an explicit UTC timezone")


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    columns = sorted(frame.columns)
    records = []
    for _, row in frame[columns].sort_values("date", kind="stable").iterrows():
        record = {}
        for column in columns:
            value = row[column]
            if column == "date":
                value = _iso(value)
            elif pd.isna(value):
                value = None
            elif isinstance(value, (float, int)) or hasattr(value, "item"):
                scalar = value.item() if hasattr(value, "item") else value
                if isinstance(scalar, float):
                    if not math.isfinite(scalar):
                        value = str(scalar)
                    else:
                        value = scalar
                else:
                    value = scalar
            else:
                value = str(value)
            record[column] = value
        records.append(record)
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _month_start(timestamp: pd.Timestamp) -> pd.Timestamp:
    value = timestamp.tz_convert("UTC")
    return pd.Timestamp(year=value.year, month=value.month, day=1, tz="UTC")


def _next_month(start: pd.Timestamp) -> pd.Timestamp:
    year = start.year + (1 if start.month == 12 else 0)
    month = 1 if start.month == 12 else start.month + 1
    return pd.Timestamp(year=year, month=month, day=1, tz="UTC")


def _read_and_summarize(path: Path, identity: dict[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]], str]:
    frame = pd.read_feather(path)
    _validate_columns(frame, identity["data_kind"])
    timestamp_field = DATA_KIND_CONTRACTS[identity["data_kind"]]["timestamp_field"]
    frame = frame.copy()
    frame[timestamp_field] = pd.to_datetime(frame[timestamp_field], utc=True)
    frame = frame.sort_values(timestamp_field, kind="stable").reset_index(drop=True)
    if frame[timestamp_field].duplicated().any():
        raise ValueError("duplicate timestamps")

    timeframe = identity["timeframe"]
    cadence_policy = DATA_KIND_CONTRACTS[identity["data_kind"]]["cadence_policy"]
    step = _parse_timeframe(timeframe) if timeframe and cadence_policy == "dense" else None
    partitions = []
    source_sha = _source_digest(path)
    dates = pd.DatetimeIndex(frame[timestamp_field])
    month = _month_start(dates.min())
    while month <= dates.max():
        end = _next_month(month)
        mask = (frame[timestamp_field] >= month) & (frame[timestamp_field] < end)
        part = frame.loc[mask].copy()
        if not part.empty:
            first = pd.Timestamp(part[timestamp_field].iloc[0])
            last = pd.Timestamp(part[timestamp_field].iloc[-1])
            missing = 0
            if step is not None:
                expected = int((last - first).total_seconds() // step) + 1
                missing = expected - len(part)
            partitions.append(
                {
                    "start": _iso(month),
                    "end": _iso(end),
                    "source_root_id": identity["source_root_id"],
                    "source_path": identity["source_path"],
                    "first_observation": _iso(first),
                    "last_observation": _iso(last),
                    "row_count": int(len(part)),
                    "source_sha256": source_sha,
                    "fingerprint": _frame_fingerprint(part),
                    "quality_status": "PARTIAL" if missing else "AVAILABLE",
                    "summary": {"internal_missing_observations": missing},
                }
            )
        month = end
    return frame, partitions, source_sha


def discover_feather_datasets(
    source_root_id: str,
    source_root: Path,
    *,
    exchange: str,
    market_type: str,
) -> list[dict[str, Any]]:
    """Inspect local Feather files without modifying them or guessing source scope."""
    if not _SOURCE_ROOT_ID.fullmatch(source_root_id):
        raise ValueError("invalid source root ID")
    root = _safe_root(source_root)
    if not exchange:
        raise ValueError("exchange is required")
    result = []
    for path in sorted(root.rglob("*.feather"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        candidate: dict[str, Any] = {
            "source_root_id": source_root_id,
            "source_path": PurePosixPath(*relative.parts).as_posix(),
            "exchange": exchange,
            "market_type": market_type,
        }
        if path.is_symlink():
            candidate.update({"status": "INVALID", "issues": ["source symlink forbidden"]})
            result.append(candidate)
            continue
        try:
            identity = _parse_filename(path.name, exchange=exchange, market_type=market_type)
            source_path, normalized_relative = _safe_source_path(root, relative)
            identity.update({"source_root_id": source_root_id, "source_path": normalized_relative})
            frame, partitions, source_sha = _read_and_summarize(source_path, identity)
            candidate.update(identity)
            candidate.update(
                {
                    "status": "PARTIAL"
                    if any(part["quality_status"] == "PARTIAL" for part in partitions)
                    else "AVAILABLE",
                    "issues": [],
                    "row_count": int(len(frame)),
                    "first_observation": _iso(frame["date"].iloc[0]),
                    "last_observation": _iso(frame["date"].iloc[-1]),
                    "source_sha256": source_sha,
                    "partitions": partitions,
                }
            )
        except (OSError, ValueError, KeyError) as exc:
            candidate.update({"status": "INVALID", "issues": [str(exc)]})
        result.append(candidate)
    return result


def register_feather_file(
    catalog_root: Path,
    *,
    source_root_id: str,
    source_root: Path,
    relative_path: Path,
    exchange: str,
    market_type: str,
    asset: str,
    exchange_symbol: str,
    data_kind: str,
    timeframe: str | None,
    source: str,
    source_reference: str = "",
) -> dict[str, Any]:
    """Register one source-root-contained Feather file without rewriting it."""
    if not _SOURCE_ROOT_ID.fullmatch(source_root_id):
        raise ValueError("invalid source root ID")
    if not source:
        raise ValueError("source is required")
    path, normalized_relative = _safe_source_path(source_root, relative_path)
    parsed = _parse_filename(path.name, exchange=exchange, market_type=market_type)
    expected = {
        "asset": asset,
        "exchange_symbol": exchange_symbol,
        "data_kind": data_kind,
        "timeframe": timeframe,
    }
    if parsed != {"exchange": exchange, "market_type": market_type, **expected}:
        raise ValueError("explicit dataset identity does not match source filename")
    identity = {
        **parsed,
        "source_root_id": source_root_id,
        "source_path": normalized_relative,
    }
    frame, partitions, _ = _read_and_summarize(path, identity)
    status = (
        "PARTIAL"
        if any(partition["quality_status"] == "PARTIAL" for partition in partitions)
        else "AVAILABLE"
    )
    dataset_id = "-".join(
        re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-").lower()
        for value in (
            exchange,
            market_type,
            asset,
            data_kind,
            timeframe or "event",
        )
    )
    manifest = {
        "schema_version": "data-catalog-v3",
        "dataset_id": dataset_id,
        "exchange": exchange,
        "market_type": market_type,
        "asset": asset,
        "exchange_symbol": exchange_symbol,
        "data_kind": data_kind,
        "timeframe": timeframe,
        "source": source,
        "source_reference": source_reference,
        "registered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "quality": {
            "status": status,
            "issues": [],
            "summary": {
                "row_count": int(len(frame)),
                "first_observation": _iso(frame["date"].iloc[0]),
                "last_observation": _iso(frame["date"].iloc[-1]),
            },
        },
        "partitions": partitions,
        "revision": {"previous_manifest_sha256": None, "reason": "initial registration"},
    }
    existing = [
        record
        for record in list_datasets(catalog_root)
        if record["manifest"]["dataset_id"] == dataset_id
    ]
    if existing:
        referenced = {
            record["manifest"]["revision"]["previous_manifest_sha256"]
            for record in existing
            if record["manifest"]["revision"]["previous_manifest_sha256"] is not None
        }
        latest = [record for record in existing if record["manifest_sha256"] not in referenced]
        if len(latest) != 1:
            raise ValueError("catalog revision lineage has no unique latest manifest")
        latest_record = latest[0]
        new_content = json.loads(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
        old_content = json.loads(
            json.dumps(latest_record["manifest"], sort_keys=True, ensure_ascii=False)
        )
        for content in (new_content, old_content):
            content.pop("registered_at", None)
            content.pop("revision", None)
        if new_content == old_content:
            return latest_record
        manifest["revision"] = {
            "previous_manifest_sha256": latest_record["manifest_sha256"],
            "reason": "source snapshot update",
        }
    return register_dataset(catalog_root, manifest)
