"""Strict bounded-CSV validation and content-addressed snapshot publishing.

This module deliberately cannot fetch network data. Agent code only reads
snapshots published by a separately authorized data maintainer.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from . import SCHEMA_VERSION

START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 1, 1, tzinfo=timezone.utc)
MARKET_FIELDS = {"open_time", "symbol", "open", "high", "low", "close", "volume", "close_time", "available_time"}
FUNDAMENTAL_FIELDS = {"asset_id", "metric", "event_time", "value"}
TIMEFRAMES = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
MANIFEST_SCHEMAS = {
    "data-catalog-v1": "catalog-manifest-v1.schema.json",
    "data-catalog-v2": "catalog-manifest-v2.schema.json",
}


@lru_cache(maxsize=2)
def _manifest_validator(version: str) -> Draft202012Validator:
    schema_name = MANIFEST_SCHEMAS.get(version)
    if schema_name is None:
        raise ValueError(f"unsupported catalog manifest schema: {version}")
    document = json.loads(
        files("data_engineering").joinpath("schemas", schema_name).read_text(encoding="utf-8")
    )
    return Draft202012Validator(document, format_checker=FormatChecker())


def validate_manifest(document: dict) -> None:
    """Validate v1/v2 Catalog metadata without modifying it."""
    version = document.get("schema_version")
    validator = _manifest_validator(version)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"catalog manifest schema invalid at {location}: {first.message}")


def _time(value: str) -> datetime:
    if not value or not isinstance(value, str):
        raise ValueError("missing time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time must have explicit timezone")
    return parsed.astimezone(timezone.utc)


def _bounded(time: datetime) -> bool:
    return START <= time < END


def _number(value: str, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("non-finite or invalid numeric value")
    return result


def validate_market(data: bytes, timeframe: str) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported timeframe")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or not MARKET_FIELDS.issubset(reader.fieldnames):
        raise ValueError(f"missing market fields: {sorted(MARKET_FIELDS - set(reader.fieldnames or []))}")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("duplicate header")
    step = TIMEFRAMES[timeframe]
    symbols: dict[str, list[datetime]] = {}
    seen = set()
    count = 0
    for row in reader:
        if None in row or not row["symbol"]:
            raise ValueError("malformed market row")
        opened, closed, available = (_time(row[k]) for k in ("open_time", "close_time", "available_time"))
        if not _bounded(opened) or closed > END:
            raise ValueError("development dataset must be wholly within [2022,2025)")
        if int(opened.timestamp()) % step or (closed - opened).total_seconds() != step:
            raise ValueError("candle timestamps do not match timeframe")
        if available < closed:
            raise ValueError("candle visible before close")
        values = {name: _number(row[name], positive=name != "volume") for name in ("open", "high", "low", "close", "volume")}
        if values["volume"] < 0 or values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
            raise ValueError("invalid OHLCV")
        key = (opened, row["symbol"])
        if key in seen:
            raise ValueError("duplicate open_time/symbol")
        seen.add(key)
        symbols.setdefault(row["symbol"], []).append(opened)
        count += 1
    if not count:
        raise ValueError("empty dataset")
    quality = {}
    for symbol, times in sorted(symbols.items()):
        times.sort()
        expected = int((times[-1] - times[0]).total_seconds() // step) + 1
        quality[symbol] = {"rows": len(times), "first": times[0].isoformat(), "last": times[-1].isoformat(),
                           "internal_missing_bars": expected - len(times), "internal_coverage": len(times) / expected}
    return {"rows": count, "timeframe": timeframe, "symbols": quality}


def validate_fundamental(data: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or not FUNDAMENTAL_FIELDS.issubset(reader.fieldnames):
        raise ValueError("missing fundamental fields")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("duplicate header")
    seen = set()
    unknown_publication = 0
    rows = 0
    metrics = set()
    for row in reader:
        asset = row.get("asset_id")
        metric = row.get("metric")
        if None in row or not asset or not metric:
            raise ValueError("malformed fundamental row")
        event = _time(row["event_time"])
        if not _bounded(event):
            raise ValueError("fundamental events outside development window")
        _number(row["value"])
        published = row.get("published_at", "").strip()
        if not published:
            unknown_publication += 1
        else:
            publish_time = _time(published)
            if publish_time < event:
                raise ValueError("published_at earlier than event_time")
        key = (asset, metric, event)
        if key in seen:
            raise ValueError("duplicate fundamental observation")
        seen.add(key)
        metrics.add(metric)
        rows += 1
    if not rows:
        raise ValueError("empty dataset")
    # A publication timestamp supplied by a vendor is *declared*, not independently audited.
    return {"rows": rows, "metrics": sorted(metrics), "publication_unknown_rows": unknown_publication,
            "point_in_time": "unverified" if unknown_publication else "declared_not_audited"}


def _atomic_create(path: Path, data: bytes) -> None:
    """Publish atomically without overwriting an existing file (including concurrent writers)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # fails closed if destination exists
    finally:
        Path(temporary).unlink(missing_ok=True)


def _paths(root: Path, dataset_id: str, digest: str) -> tuple[Path, Path]:
    if not ID.fullmatch(dataset_id) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("invalid dataset id or digest")
    parent = root / "snapshots" / dataset_id
    return parent / f"{digest}.csv", parent / f"{digest}.manifest.json"


def publish(root: Path, *, dataset_id: str, kind: str, source: str, input_path: Path,
            timeframe: str | None = None, source_reference: str = "") -> dict:
    """Create a development-only snapshot; source must already be physically bounded.

    Never point this at a mixed 2022-2026 file. This process reads the entire input.
    """
    if not source or not isinstance(source, str):
        raise ValueError("source is required")
    if not input_path.is_file() or input_path.is_symlink():
        raise ValueError("source must be a regular non-symlink file")
    if input_path.stat().st_size > 2_000_000_000:
        raise ValueError("source too large for single-file CSV importer")
    data = input_path.read_bytes()
    if kind == "market":
        quality = validate_market(data, timeframe or "")
    elif kind == "fundamental":
        if timeframe is not None:
            raise ValueError("fundamental snapshot has no candle timeframe")
        quality = validate_fundamental(data)
    else:
        raise ValueError("unsupported data kind")
    digest = hashlib.sha256(data).hexdigest()
    csv_path, manifest_path = _paths(Path(root), dataset_id, digest)
    if csv_path.exists() or manifest_path.exists():
        previous = verify(root, dataset_id=dataset_id, digest=digest)
        if (previous["kind"] != kind or previous["source"] != source
                or previous["source_reference"] != source_reference
                or previous["quality"] != quality):
            raise ValueError("same content already registered under conflicting provenance")
        return previous
    manifest = {"schema_version": SCHEMA_VERSION, "dataset_id": dataset_id, "kind": kind,
                "source": source, "source_reference": source_reference,
                "access_class": "EXPLORATORY_2022_2024", "sha256": digest,
                "bytes": len(data), "quality": quality,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "research_safe": kind == "market", "note": (
                    "Only pre-bounded development data; verify source provenance before using."
                    if kind == "market" else
                    "Historical values are NOT independently verified as point-in-time; do not use in strict PIT inference.")}
    validate_manifest(manifest)
    _atomic_create(csv_path, data)
    _atomic_create(manifest_path, (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    return manifest


def verify(root: Path, *, dataset_id: str, digest: str) -> dict:
    csv_path, manifest_path = _paths(Path(root), dataset_id, digest)
    if csv_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("snapshot symlink forbidden")
    if not csv_path.is_file() or not manifest_path.is_file():
        raise ValueError("missing snapshot or manifest")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(document)
    if document.get("sha256") != digest or document.get("dataset_id") != dataset_id:
        raise ValueError("manifest identity mismatch")
    data = csv_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest or len(data) != document.get("bytes"):
        raise ValueError("snapshot integrity violation")
    check = (validate_market(data, document["quality"]["timeframe"])
             if document["kind"] == "market" else validate_fundamental(data))
    if check != document["quality"]:
        raise ValueError("snapshot quality mismatch")
    return document


def export_market(root: Path, *, dataset_id: str, digest: str, output: Path) -> Path:
    meta = verify(root, dataset_id=dataset_id, digest=digest)
    if meta["kind"] != "market" or not meta["research_safe"]:
        raise ValueError("fundamental or unverified data cannot be exported as a market panel")
    if output.exists() or output.is_symlink():
        raise FileExistsError("never overwrite a research panel")
    csv_path, _ = _paths(Path(root), dataset_id, digest)
    _atomic_create(output, csv_path.read_bytes())
    return output
