"""Read-only adapter for pinned, bounded fundamental Catalog snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal, Sequence

import pandas as pd

from .catalog import END, START, verify
from .registries import AssetRegistry, MetricRegistry

_STRICT_PIT_READ_TOKEN = object()


def _validated_max_age(max_age: str) -> pd.Timedelta:
    if not isinstance(max_age, str) or not max_age.strip():
        raise ValueError("max_age must be an explicit positive duration")
    try:
        age = pd.Timedelta(max_age)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_age must be a valid duration") from exc
    if pd.isna(age) or age < pd.Timedelta(0):
        raise ValueError("max_age must be non-negative")
    return age

OUTPUT_COLUMNS = [
    "asset_id", "tradable_asset_id", "entity_scope", "metric", "event_time",
    "available_time", "value", "source", "dataset_id", "snapshot_sha256",
    "pit_status",
]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    canonical = frame[OUTPUT_COLUMNS].copy()
    canonical["event_time"] = pd.to_datetime(canonical.event_time, utc=True)
    canonical["available_time"] = pd.to_datetime(canonical.available_time, utc=True)
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(canonical, index=False).values.tobytes())
    provenance = {
        key: frame.attrs.get(key)
        for key in (
            "access_mode", "asset_registry_version", "metric_registry_version",
            "snapshot_pins", "pit_attestation_sha256",
        )
    }
    digest.update(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotRef:
    dataset_id: str
    snapshot_sha256: str
    source: str
    kind: str
    point_in_time: str


class FundamentalDataAdapter:
    """Reads Catalog snapshots without modifying or copying Catalog files."""

    def __init__(self, root: str | Path = Path("user_data/data_engineering")):
        self.root = Path(root)
        self.assets = AssetRegistry()
        self.metrics = MetricRegistry()

    def _safe_path(self, path: Path) -> Path:
        root = self.root.absolute()
        candidate = path.absolute()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Catalog path escaped configured root") from exc
        current = root
        if current.is_symlink():
            raise ValueError("Catalog root symlink is forbidden")
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Catalog symlink is forbidden")
        if not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError("Catalog path escaped configured root")
        return candidate

    def _snapshot_paths(self, dataset_id: str, digest: str) -> tuple[Path, Path]:
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError("dataset_id and snapshot_sha256 are required")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("dataset_id and snapshot_sha256 are required")
        directory = self._safe_path(self.root / "snapshots" / dataset_id)
        csv_path = self._safe_path(directory / f"{digest}.csv")
        manifest_path = self._safe_path(directory / f"{digest}.manifest.json")
        return csv_path, manifest_path

    def list_snapshots(self) -> list[SnapshotRef]:
        snapshots = self._safe_path(self.root / "snapshots")
        if not snapshots.is_dir():
            raise FileNotFoundError(f"Catalog snapshots directory missing: {snapshots}")
        result = []
        for dataset_dir in sorted(snapshots.iterdir()):
            if dataset_dir.is_symlink():
                raise ValueError("Catalog symlink is forbidden")
            if not dataset_dir.is_dir():
                continue
            for manifest_path in sorted(dataset_dir.glob("*.manifest.json")):
                self._safe_path(manifest_path)
                digest = manifest_path.name.removesuffix(".manifest.json")
                manifest = verify(self.root, dataset_id=dataset_dir.name, digest=digest)
                if manifest["kind"] == "fundamental":
                    normalized = self.read(
                        dataset_dir.name, digest, access="exploratory"
                    )
                    result.append(SnapshotRef(
                        dataset_id=dataset_dir.name, snapshot_sha256=digest,
                        source=manifest["source"], kind=manifest["kind"],
                        point_in_time=(normalized.pit_status.iloc[0]
                                       if not normalized.empty else "unknown"),
                    ))
        return result

    def read_many(
        self,
        snapshots: Sequence[SnapshotRef | tuple[str, str]],
        *,
        access: Literal["exploratory", "strict_pit"],
        start=START,
        end=END,
    ) -> pd.DataFrame:
        """Read and validate each explicitly pinned snapshot before combining."""
        if not snapshots:
            raise ValueError("at least one pinned snapshot is required")
        pins = [
            (snapshot.dataset_id, snapshot.snapshot_sha256)
            if isinstance(snapshot, SnapshotRef) else tuple(snapshot)
            for snapshot in snapshots
        ]
        if any(len(pin) != 2 for pin in pins):
            raise ValueError("each snapshot pin must contain dataset_id and SHA-256")
        if len(set(pins)) != len(pins):
            raise ValueError("duplicate snapshot pin")
        if len({dataset_id for dataset_id, _ in pins}) != len(pins):
            raise ValueError("pin at most one version per dataset_id")
        frames = [
            self.read(dataset_id, digest, access=access, start=start, end=end)
            for dataset_id, digest in pins
        ]
        result = pd.concat(frames, ignore_index=True)
        result.attrs.update({
            "asset_registry_version": self.assets.version,
            "metric_registry_version": self.metrics.version,
            "access_mode": access,
            "snapshot_pins": sorted(pins),
            "pit_attestation_sha256": sorted(
                digest for frame in frames
                if (digest := frame.attrs.get("pit_attestation_sha256"))
            ),
        })
        if access == "strict_pit":
            result.attrs["_strict_pit_read_token"] = _STRICT_PIT_READ_TOKEN
            result.attrs["_strict_pit_read_fingerprint"] = _frame_fingerprint(result)
        return result

    @staticmethod
    def _bound(value, name: str) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{name} must include a timezone")
        return timestamp.tz_convert("UTC")

    @staticmethod
    def _parse_actual_time(value, column: str, row_number: int):
        if value is None or pd.isna(value) or not str(value).strip():
            return pd.NaT
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{column} must include a timezone at row {row_number}")
        return timestamp.tz_convert("UTC")

    def _attestation_status(
        self, manifest: dict, frame: pd.DataFrame,
        availability_column: str | None,
    ) -> tuple[str, str | None]:
        attestation_dir = self._safe_path(self.root / "pit_attestations")
        if not attestation_dir.is_dir():
            return manifest["quality"].get("point_in_time", "unverified"), None
        matches = []
        for evidence_path in sorted(attestation_dir.glob("*.json")):
            self._safe_path(evidence_path)
            evidence_digest = evidence_path.stem
            if not SHA256.fullmatch(evidence_digest):
                continue
            evidence_bytes = evidence_path.read_bytes()
            if hashlib.sha256(evidence_bytes).hexdigest() != evidence_digest:
                continue
            try:
                evidence = json.loads(evidence_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (evidence.get("dataset_id") == manifest["dataset_id"]
                    and evidence.get("snapshot_sha256") == manifest["sha256"]):
                matches.append((evidence_digest, evidence))
        if not matches:
            return manifest["quality"].get("point_in_time", "unverified"), None
        if len(matches) != 1:
            raise ValueError("multiple PIT attestations match the same snapshot")
        evidence_digest, evidence = matches[0]
        required = {"reviewer", "audit_reference"}
        if required - set(evidence):
            raise ValueError("invalid PIT attestation evidence fields")
        if not evidence["reviewer"] or not evidence["audit_reference"]:
            raise ValueError("PIT attestation reviewer and audit reference are required")
        if (
            evidence.get("status") != "verified"
            or evidence.get("availability_column") != availability_column
        ):
            raise ValueError("PIT attestation evidence does not match availability schema")
        if availability_column is None or frame.available_time.isna().any():
            raise ValueError("verified PIT data requires actual availability times")
        return "verified", evidence_digest

    def read(
        self,
        dataset_id: str,
        snapshot_sha256: str,
        *,
        access: Literal["exploratory", "strict_pit"],
        start=START,
        end=END,
    ) -> pd.DataFrame:
        if access not in {"exploratory", "strict_pit"}:
            raise ValueError("access must be exploratory or strict_pit")
        start_time = self._bound(start, "start")
        end_time = self._bound(end, "end")
        if (
            start_time < pd.Timestamp(START)
            or end_time > pd.Timestamp(END)
            or start_time >= end_time
        ):
            raise ValueError("read range must be inside development window")
        csv_path, _ = self._snapshot_paths(dataset_id, snapshot_sha256)
        manifest = verify(self.root, dataset_id=dataset_id, digest=snapshot_sha256)
        if manifest["kind"] != "fundamental":
            raise ValueError("snapshot is not a fundamental snapshot")
        frame = pd.read_csv(csv_path, dtype={"asset_id": "string", "metric": "string"})
        required = {"asset_id", "metric", "event_time", "value"}
        if required - set(frame.columns):
            raise ValueError("fundamental snapshot schema is incomplete")
        if not frame.columns.is_unique:
            raise ValueError("duplicate fundamental columns")
        if frame.duplicated(["asset_id", "metric", "event_time"]).any():
            raise ValueError("duplicate fundamental observation")
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="raise")
        has_available = "available_time" in frame
        has_published = "published_at" in frame
        availability_column = (
            "available_time_or_published_at" if has_available and has_published else
            "available_time" if has_available else
            "published_at" if has_published else None
        )
        if has_available and has_published:
            for row_number, (actual, published) in enumerate(
                zip(frame.available_time, frame.published_at), start=2
            ):
                actual_time = self._parse_actual_time(actual, "available_time", row_number)
                published_time = self._parse_actual_time(published, "published_at", row_number)
                if (
                    not pd.isna(actual_time) and not pd.isna(published_time)
                    and actual_time != published_time
                ):
                    raise ValueError("available_time conflicts with published_at")
        if has_available and has_published:
            raw_availability = [
                actual if actual is not None and not pd.isna(actual) and str(actual).strip()
                else published
                for actual, published in zip(frame.available_time, frame.published_at)
            ]
        elif availability_column is not None:
            raw_availability = frame[availability_column].tolist()
        else:
            raw_availability = [None] * len(frame)
        frame["available_time"] = [
            self._parse_actual_time(value, availability_column or "available_time", i + 2)
            for i, value in enumerate(raw_availability)
        ]
        known_availability = frame.available_time.dropna()
        if not known_availability.empty and (
            (known_availability < pd.Timestamp(START)).any()
            or (known_availability >= pd.Timestamp(END)).any()
        ):
            raise ValueError("availability time outside development window")
        known_pairs = frame.available_time.notna()
        if known_pairs.any() and (frame.loc[known_pairs, "available_time"]
                                  < frame.loc[known_pairs, "event_time"]).any():
            raise ValueError("availability time precedes event time")
        pit_status, attestation_digest = self._attestation_status(
            manifest, frame, availability_column
        )
        definitions = [
            self.metrics.resolve_source(manifest["source"], str(row.metric))
            for row in frame.itertuples(index=False)
        ]
        identities = [
            self.assets.resolve_source_id(
                manifest["source"], str(row.asset_id),
                entity_scope=definition.entity_scope,
            )
            for row, definition in zip(frame.itertuples(index=False), definitions)
        ]
        frame["asset_id"] = [identity.asset_id for identity in identities]
        frame["tradable_asset_id"] = [identity.tradable_asset_id for identity in identities]
        frame["entity_scope"] = [identity.entity_scope for identity in identities]
        frame["metric"] = [definition.metric for definition in definitions]
        if frame.value.isna().any():
            raise ValueError("fundamental values must be present and numeric")
        frame["value"] = pd.to_numeric(frame.value, errors="raise").astype(float)
        if not frame.value.map(math.isfinite).all():
            raise ValueError("fundamental values must be finite")
        frame["source"] = manifest["source"]
        frame["dataset_id"] = dataset_id
        frame["snapshot_sha256"] = snapshot_sha256
        frame["pit_status"] = pit_status
        frame = frame.loc[frame.event_time.ge(start_time) & frame.event_time.lt(end_time)]
        if access == "strict_pit" and (
            pit_status != "verified" or frame.available_time.isna().any()
        ):
            raise ValueError("strict_pit rejects PIT-unverified or unavailable data")
        result = frame[OUTPUT_COLUMNS].sort_values(
            ["event_time", "asset_id", "metric"]
        ).reset_index(drop=True)
        result.attrs.update({
            "asset_registry_version": self.assets.version,
            "metric_registry_version": self.metrics.version,
            "access_mode": access,
        })
        if access == "strict_pit":
            result.attrs["_strict_pit_read_token"] = _STRICT_PIT_READ_TOKEN
            result.attrs["pit_attestation_sha256"] = attestation_digest
            result.attrs["snapshot_pins"] = [(dataset_id, snapshot_sha256)]
            result.attrs["_strict_pit_read_fingerprint"] = _frame_fingerprint(result)
        return result


def asof_join(
    panel,
    observations: pd.DataFrame,
    *,
    metric: str,
    max_age: str,
) -> pd.DataFrame:
    """Join only PIT-verified observations available at each market decision."""
    age = _validated_max_age(max_age)
    required = set(OUTPUT_COLUMNS)
    if required - set(observations.columns):
        raise ValueError("observations are missing normalized adapter columns")
    if (observations.attrs.get("access_mode") != "strict_pit"
            or observations.attrs.get("_strict_pit_read_token") is not _STRICT_PIT_READ_TOKEN):
        raise ValueError("as-of join requires observations read in strict_pit mode")
    if (observations.attrs.get("_strict_pit_read_fingerprint")
            != _frame_fingerprint(observations)):
        raise ValueError("strict PIT observations changed after adapter validation")
    source = observations.loc[observations.metric.eq(metric)].copy()
    if source.empty:
        raise ValueError(f"no verified observations for metric {metric}")
    if source.pit_status.ne("verified").any() or source.available_time.isna().any():
        raise ValueError("strict as-of join requires verified availability times")

    assets = AssetRegistry()
    metrics = MetricRegistry()
    for row in source.itertuples(index=False):
        if not assets.matches_identity(
            row.source, row.asset_id, row.tradable_asset_id, row.entity_scope
        ):
            raise ValueError("observation economic identity does not match registry")
        metrics.resolve(row.source, row.metric, row.entity_scope)
    source["available_time"] = pd.to_datetime(source.available_time, utc=True, errors="raise")
    source["event_time"] = pd.to_datetime(source.event_time, utc=True, errors="raise")
    if source.duplicated(["tradable_asset_id", "metric", "available_time"]).any():
        raise ValueError("ambiguous observations share an availability time")

    market = panel.frame.reset_index().copy()
    market["tradable_asset_id"] = market.symbol.map(assets.resolve_symbol)
    market["decision_time"] = pd.to_datetime(market.available_time, utc=True, errors="raise")
    window_start = pd.Timestamp(START)
    window_end = pd.Timestamp(END)
    panel_times = pd.DatetimeIndex(market.open_time)
    if (
        (panel_times < window_start).any() or (panel_times >= window_end).any()
        or (market.decision_time < window_start).any()
        or (market.decision_time >= window_end).any()
        or (source.event_time < window_start).any()
        or (source.event_time >= window_end).any()
        or (source.available_time < window_start).any()
        or (source.available_time >= window_end).any()
    ):
        raise ValueError("as-of join data must stay inside development window")
    market["_row_order"] = range(len(market))
    source = source.rename(columns={
        "available_time": "feature_available_time",
        "value": "fundamental_value",
    })
    left = market.sort_values(["decision_time", "tradable_asset_id"])
    right = source.sort_values(["feature_available_time", "tradable_asset_id"])
    joined = pd.merge_asof(
        left, right,
        left_on="decision_time", right_on="feature_available_time",
        by="tradable_asset_id", direction="backward", tolerance=age,
        allow_exact_matches=True,
    ).sort_values("_row_order")
    joined.index = panel.frame.index
    return joined[[
        "fundamental_value", "feature_available_time", "asset_id", "entity_scope",
        "metric", "source", "dataset_id", "snapshot_sha256", "pit_status",
    ]]
