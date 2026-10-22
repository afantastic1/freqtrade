"""Read-only target inventory for Data Catalog v3 expansion planning."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Mapping

from .catalog_v3 import get_coverage, validate_dataset
from .sources.contracts import DatasetIdentity, DatasetRequest


_TARGET_FIELDS = {
    "exchange", "market_type", "asset", "exchange_symbol", "data_kind",
    "timeframe", "start", "end",
}
_DATA_KINDS = {"ohlcv", "funding_rate", "open_interest", "mark_price"}
_MARKET_TYPES = {"spot", "futures"}
_TIMEFRAME = re.compile(r"^[1-9][0-9]*(?:s|m|h|d|w)$")


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timezone-aware UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a timezone-aware UTC timestamp") from exc
    if (parsed.tzinfo is None or parsed.utcoffset() is None
            or parsed.utcoffset().total_seconds() != 0):
        raise ValueError(f"{name} must be a timezone-aware UTC timestamp")
    return parsed


def _parse_target(raw: object, index: int) -> DatasetRequest:
    if not isinstance(raw, Mapping):
        raise ValueError(f"target {index} must be an object")
    unknown = set(raw) - _TARGET_FIELDS
    missing = _TARGET_FIELDS - set(raw)
    if unknown:
        raise ValueError(f"target {index} contains unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"target {index} is missing fields: {sorted(missing)}")
    if raw["market_type"] not in _MARKET_TYPES:
        raise ValueError(f"target {index} has unsupported market_type")
    if raw["data_kind"] not in _DATA_KINDS:
        raise ValueError(f"target {index} has unsupported data_kind")
    if not isinstance(raw["timeframe"], str) or not _TIMEFRAME.fullmatch(raw["timeframe"]):
        raise ValueError(f"target {index} has invalid timeframe")
    identity = DatasetIdentity(
        exchange=raw["exchange"], market_type=raw["market_type"],
        asset=raw["asset"], exchange_symbol=raw["exchange_symbol"],
        data_kind=raw["data_kind"], timeframe=raw["timeframe"],
    )
    return DatasetRequest(
        identity=identity, start=_utc(raw["start"], "start"),
        end=_utc(raw["end"], "end"), request_id=f"catalog-audit-{index}",
    )


def _identity_record(identity: DatasetIdentity) -> dict[str, str]:
    return {
        "exchange": identity.exchange,
        "market_type": identity.market_type,
        "asset": identity.asset,
        "exchange_symbol": identity.exchange_symbol,
        "data_kind": identity.data_kind,
        "timeframe": identity.timeframe,
    }


def _issue_codes(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return sorted({
        str(item.get("code", "UNKNOWN"))
        for item in items if isinstance(item, Mapping)
    })


def _manifest_matches(identity: DatasetIdentity, manifest: object) -> bool:
    if not isinstance(manifest, Mapping):
        return False
    for field in ("exchange", "market_type", "asset", "exchange_symbol", "data_kind"):
        if manifest.get(field) != getattr(identity, field):
            return False
    if identity.data_kind in {"ohlcv", "mark_price"}:
        return manifest.get("timeframe") == identity.timeframe
    cadence = manifest.get("event_cadence")
    return manifest.get("timeframe") is None and cadence in {None, identity.timeframe}


def audit_targets(
    catalog_root: Path,
    source_roots: Mapping[str, Path],
    raw_targets: object,
) -> dict:
    """Classify requested datasets without downloading or mutating Catalog state."""
    if not isinstance(raw_targets, Mapping) or set(raw_targets) != {"targets"}:
        raise ValueError("target document must contain exactly targets")
    values = raw_targets["targets"]
    if not isinstance(values, list) or not values:
        raise ValueError("targets must be a nonempty list")
    requests = [_parse_target(raw, index) for index, raw in enumerate(values)]
    identities = [request.identity.dataset_id() for request in requests]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate target identity")

    rows = []
    for request in requests:
        dataset_id = request.identity.dataset_id()
        verified = validate_dataset(
            Path(catalog_root), dataset_id, source_roots=source_roots
        )
        status = verified["status"]
        row = {
            "dataset_id": dataset_id,
            "identity": _identity_record(request.identity),
            "requested_interval": {
                "start": request.start.isoformat(), "end": request.end.isoformat(),
            },
            "status": status,
            "skip_download": False,
            "issue_codes": _issue_codes(verified.get("issues", [])),
            "missing_interval_count": 0,
            "missing_intervals": [],
        }
        if status != "MISSING" and not _manifest_matches(
            request.identity, verified.get("manifest")
        ):
            row["status"] = "INVALID"
            row["issue_codes"] = ["DATASET_IDENTITY_MISMATCH"]
            rows.append(row)
            continue
        if status not in {"MISSING", "INVALID"}:
            coverage = get_coverage(
                Path(catalog_root), dataset_id, request.start, request.end,
                source_roots=source_roots,
            )
            coverage_status = coverage["status"]
            row["status"] = "PRESENT" if coverage_status == "AVAILABLE" else coverage_status
            row["skip_download"] = coverage_status == "AVAILABLE"
            row["issue_codes"] = _issue_codes(coverage.get("issues", []))
            row["missing_interval_count"] = coverage.get("missing_interval_count", 0)
            row["missing_intervals"] = [
                [item["start"].replace("Z", "+00:00"), item["end"].replace("Z", "+00:00")]
                for item in coverage.get("missing_intervals", [])
            ]
        rows.append(row)

    rows.sort(key=lambda item: item["dataset_id"])
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("INVALID", "MISSING", "PARTIAL", "PRESENT")}
    return {
        "read_only": True,
        "summary": {**counts, "target_count": len(rows)},
        "targets": rows,
    }


__all__ = ["audit_targets"]
