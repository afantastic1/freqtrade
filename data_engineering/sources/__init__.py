"""Shared contracts and adapters for preparing immutable research datasets."""

from .contracts import (
    AdapterCapability,
    AvailabilityStatus,
    DataSourceAdapter,
    DatasetIdentity,
    DatasetRequest,
    DownloadResult,
    DownloadStatus,
    ObservationBatch,
    SourceArtifact,
    UnsupportedCapabilityError,
    ValidationReceipt,
    download_artifact,
)

__all__ = [
    "AdapterCapability",
    "AvailabilityStatus",
    "DataSourceAdapter",
    "DatasetIdentity",
    "DatasetRequest",
    "DownloadResult",
    "DownloadStatus",
    "ObservationBatch",
    "SourceArtifact",
    "UnsupportedCapabilityError",
    "ValidationReceipt",
    "download_artifact",
]
