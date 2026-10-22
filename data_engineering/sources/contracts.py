"""Typed contracts shared by local and remote data source adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class AdapterCapability(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    DOWNLOAD = "download"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class DownloadStatus(StrEnum):
    DOWNLOADED = "DOWNLOADED"
    ALREADY_CACHED = "ALREADY_CACHED"
    UNAVAILABLE = "UNAVAILABLE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    CHECKSUM_FAILED = "CHECKSUM_FAILED"


class UnsupportedCapabilityError(RuntimeError):
    """Raised when a caller requests a source operation the adapter lacks."""


def _validate_identity_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_utc_bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("start and end must be datetime values")
    for name, value in (("start", start), ("end", end)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError(f"{name} must be timezone-aware UTC")
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")
    return start_utc, end_utc


def _validate_digest(value: str, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """Stable identity independent of a request's date range or source path."""

    exchange: str
    market_type: str
    asset: str
    exchange_symbol: str
    data_kind: str
    timeframe: str | None
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        for name in (
            "exchange", "market_type", "asset", "exchange_symbol",
            "data_kind", "schema_version",
        ):
            _validate_identity_text(getattr(self, name), name)
        if self.timeframe is not None:
            _validate_identity_text(self.timeframe, "timeframe")

    def dataset_id(self) -> str:
        """Return the PR-02 ID for v1, versioned when schema identity changes."""
        symbol_hash = hashlib.sha256(self.exchange_symbol.encode("utf-8")).hexdigest()[:12]
        parts = (
            self.exchange, self.market_type, self.asset, self.data_kind,
            self.timeframe or "event", symbol_hash,
        )
        slug = "-".join(
            re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
            for value in parts
        )
        if self.schema_version == "v1":
            return slug
        version_hash = hashlib.sha256(self.schema_version.encode("utf-8")).hexdigest()[:12]
        return f"{slug}-schema-{version_hash}"


@dataclass(frozen=True, slots=True)
class DatasetRequest:
    """One bounded request for a stable dataset identity."""

    identity: DatasetIdentity
    start: datetime
    end: datetime
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DatasetIdentity):
            raise ValueError("identity must be a DatasetIdentity")
        _validate_identity_text(self.request_id, "request_id")
        start, end = _validate_utc_bounds(self.start, self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """A source object or bounded view of a source object."""

    identity: DatasetIdentity
    request_id: str
    source_locator: str
    source_format: str
    start: datetime
    end: datetime
    availability: AvailabilityStatus | str
    expected_checksum: str | None = None
    local_path: Path | None = None
    source_root_id: str | None = None
    relative_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DatasetIdentity):
            raise ValueError("identity must be a DatasetIdentity")
        for name in ("request_id", "source_locator", "source_format"):
            _validate_identity_text(getattr(self, name), name)
        start, end = _validate_utc_bounds(self.start, self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        try:
            status = AvailabilityStatus(self.availability)
        except ValueError as exc:
            raise ValueError("availability must be AVAILABLE, UNAVAILABLE, or UNKNOWN") from exc
        object.__setattr__(self, "availability", status)
        if self.expected_checksum is not None:
            _validate_digest(self.expected_checksum, "expected_checksum")
        if self.local_path is not None:
            object.__setattr__(self, "local_path", Path(self.local_path))
        if self.source_root_id is not None:
            _validate_identity_text(self.source_root_id, "source_root_id")
        if self.relative_path is not None:
            _validate_identity_text(self.relative_path, "relative_path")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    """Parsed observations while retaining source ordering and lineage."""

    identity: DatasetIdentity
    artifact: SourceArtifact
    observations: Any
    source_sha256: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.identity != self.artifact.identity:
            raise ValueError("batch and artifact dataset identities must match")
        _validate_digest(self.source_sha256, "source_sha256")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Outcome of attempting to acquire one source artifact."""

    status: DownloadStatus
    local_path: Path | None = None
    issue_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        try:
            status = DownloadStatus(self.status)
        except ValueError as exc:
            raise ValueError("invalid download status") from exc
        object.__setattr__(self, "status", status)
        if self.local_path is not None:
            object.__setattr__(self, "local_path", Path(self.local_path))
        if status in {DownloadStatus.DOWNLOAD_FAILED, DownloadStatus.CHECKSUM_FAILED} and not self.issue_code:
            raise ValueError("failed downloads require an issue_code")
        if status in {DownloadStatus.DOWNLOADED, DownloadStatus.ALREADY_CACHED} and self.local_path is None:
            raise ValueError("successful downloads require a local_path")


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    """Digest-bound approval for a specific normalized artifact and interval."""

    dataset_id: str
    request_id: str
    source_sha256: str
    normalized_sha256: str
    converter_version: str
    schema_version: str
    start: datetime
    end: datetime
    validation_fingerprint: str
    quality_status: str = "AVAILABLE"
    issue_codes: tuple[str, ...] = ()
    row_count: int = 0
    content_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in ("dataset_id", "request_id", "converter_version", "schema_version"):
            _validate_identity_text(getattr(self, name), name)
        for name in ("source_sha256", "normalized_sha256", "validation_fingerprint"):
            _validate_digest(getattr(self, name), name)
        if self.content_fingerprint is not None:
            _validate_digest(self.content_fingerprint, "content_fingerprint")
        if self.quality_status not in {"AVAILABLE", "PARTIAL", "INVALID", "MISSING"}:
            raise ValueError("invalid validation quality_status")
        if self.row_count < 0:
            raise ValueError("row_count must not be negative")
        object.__setattr__(self, "issue_codes", tuple(self.issue_codes))
        start, end = _validate_utc_bounds(self.start, self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@runtime_checkable
class DataSourceAdapter(Protocol):
    """Common metadata/read contract with explicitly declared capabilities."""

    name: str
    capabilities: frozenset[AdapterCapability]

    def discover(self, request: DatasetRequest) -> list[SourceArtifact]: ...

    def read(self, artifact: SourceArtifact) -> ObservationBatch: ...

    def download(self, artifact: SourceArtifact, destination: Path) -> DownloadResult: ...


def download_artifact(
    adapter: DataSourceAdapter, artifact: SourceArtifact, destination: Path
) -> DownloadResult:
    """Dispatch download only if the adapter opted into that capability."""
    capabilities = {AdapterCapability(item) for item in adapter.capabilities}
    if AdapterCapability.DOWNLOAD not in capabilities:
        raise UnsupportedCapabilityError(
            f"adapter {getattr(adapter, 'name', type(adapter).__name__)} does not support download"
        )
    return adapter.download(artifact, Path(destination))
