"""Interval coverage queries for verified Data Catalog v3 datasets."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import statistics
from typing import Any, Mapping

import pandas as pd

from .data_kind_registry import DATA_KIND_CONTRACTS


_MAX_REPORTED_GAPS = 100


def _utc(value: datetime, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return stamp.tz_convert("UTC")


def _iso(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _step_seconds(timeframe: str | None) -> int | None:
    if timeframe is None:
        return None
    return int(pd.Timedelta(timeframe).total_seconds())


def _latest_record(root: Path, dataset_id: str) -> dict[str, Any] | None:
    from .catalog_v3 import list_datasets

    records = [r for r in list_datasets(root) if r["manifest"]["dataset_id"] == dataset_id]
    if not records:
        return None
    parents = {
        r["manifest"]["revision"]["previous_manifest_sha256"]
        for r in records
        if r["manifest"]["revision"]["previous_manifest_sha256"] is not None
    }
    leaves = [r for r in records if r["manifest_sha256"] not in parents]
    if len(leaves) != 1:
        raise ValueError("catalog revision lineage has no unique latest manifest")
    return leaves[0]


def get_coverage(
    root: Path,
    dataset_id: str,
    start: datetime,
    end: datetime,
    *,
    source_roots: Mapping[str, Path],
) -> dict[str, Any]:
    """Report coverage for a requested half-open UTC interval."""
    start_utc, end_utc = _utc(start, "start"), _utc(end, "end")
    if start_utc >= end_utc:
        raise ValueError("start must be before end")
    from .catalog_v3 import validate_dataset

    verified = validate_dataset(root, dataset_id, source_roots=dict(source_roots))
    base: dict[str, Any] = {
        "dataset_id": dataset_id,
        "requested_interval": {"start": _iso(start_utc), "end": _iso(end_utc)},
        "observed_intervals": [],
        "missing_intervals": [],
        "missing_interval_count": 0,
        "missing_observations": 0,
        "observed_count": 0,
        "partition_versions": [],
        "observed_cadence": {"median_seconds": None, "min_seconds": None, "max_seconds": None},
    }
    if verified["status"] == "MISSING":
        return {**base, "status": "MISSING"}
    if verified["status"] == "INVALID":
        return {**base, "status": "INVALID", "issues": verified["issues"]}

    record = _latest_record(root, dataset_id)
    assert record is not None
    manifest = record["manifest"]
    base["partition_versions"] = [
        {"manifest_sha256": record["manifest_sha256"],
         "fingerprint": part["fingerprint"], "source_sha256": part["source_sha256"],
         "revision": manifest["revision"]}
        for part in manifest["partitions"]
        if pd.Timestamp(part["end"]) > start_utc and pd.Timestamp(part["start"]) < end_utc
    ]

    from .freqtrade_feather import _safe_source_path

    source_frames = []
    visited: set[tuple[str, str]] = set()
    for part in manifest["partitions"]:
        source_key = (part["source_root_id"], part["source_path"])
        if source_key in visited:
            continue
        visited.add(source_key)
        configured_root = source_roots.get(source_key[0])
        if configured_root is None:
            continue
        source_path, _ = _safe_source_path(configured_root, Path(source_key[1]))
        frame = pd.read_feather(source_path)
        stamps = pd.to_datetime(frame["date"], utc=True)
        selected = frame.loc[(stamps >= start_utc) & (stamps < end_utc)].copy()
        if not selected.empty:
            selected["date"] = pd.to_datetime(selected["date"], utc=True)
            source_frames.append(selected)
    if not source_frames:
        return {**base, "status": "MISSING"}

    observations = pd.concat(source_frames, ignore_index=True).sort_values("date", kind="stable")
    observations = observations.drop_duplicates("date", keep="first")
    stamps = pd.DatetimeIndex(observations["date"])
    base["observed_count"] = len(stamps)
    seconds = [float((right - left).total_seconds()) for left, right in zip(stamps[:-1], stamps[1:])]
    if seconds:
        base["observed_cadence"] = {
            "median_seconds": statistics.median(seconds),
            "min_seconds": min(seconds),
            "max_seconds": max(seconds),
        }

    contract = DATA_KIND_CONTRACTS[manifest["data_kind"]]
    if contract["cadence_policy"] != "dense" or manifest["timeframe"] is None:
        base["status"] = "PARTIAL"
        base["coverage_basis"] = "observed_only"
        base["observed_intervals"] = [{"start": _iso(stamp), "end": _iso(stamp)} for stamp in stamps]
        return base

    step_seconds = _step_seconds(manifest["timeframe"])
    assert step_seconds is not None
    step = pd.Timedelta(seconds=step_seconds)
    epoch = pd.Timestamp("1970-01-01T00:00:00Z")
    first_expected = epoch + ((start_utc - epoch + step - pd.Timedelta(nanoseconds=1)) // step) * step
    expected = pd.date_range(first_expected, end_utc, freq=step, inclusive="left")
    present = set(stamps.asi8)
    absent = [stamp for stamp in expected if stamp.value not in present]
    base["missing_observations"] = len(absent)

    def group_runs(values: list[pd.Timestamp]) -> list[list[pd.Timestamp]]:
        runs: list[list[pd.Timestamp]] = []
        for value in values:
            if not runs or value - runs[-1][-1] != step:
                runs.append([value])
            else:
                runs[-1].append(value)
        return runs

    present_expected = [stamp for stamp in expected if stamp.value in present]
    observed_runs = group_runs(present_expected)
    missing_runs = group_runs(absent)
    base["observed_intervals"] = [
        {"start": _iso(run[0]), "end": _iso(run[-1] + step)} for run in observed_runs
    ]
    base["missing_interval_count"] = len(missing_runs)
    base["missing_intervals"] = [
        {"start": _iso(run[0]), "end": _iso(run[-1] + step)}
        for run in missing_runs[:_MAX_REPORTED_GAPS]
    ]
    base["status"] = "AVAILABLE" if not absent else "PARTIAL"
    return base
