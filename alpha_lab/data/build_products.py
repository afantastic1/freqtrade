"""Separate worker-visible inputs, evaluator labels, and build metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

import pandas as pd

from .availability import asof_join


def _copy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("product frame must be a DataFrame")
    return frame.copy(deep=True)


@dataclass(frozen=True)
class WorkerInputV2:
    _frame: pd.DataFrame = field(repr=False)
    availability_columns: tuple[str, ...]

    def __post_init__(self):
        frame = _copy_frame(self._frame)
        if any("label" in str(column).lower() for column in frame.columns):
            raise ValueError("worker input cannot contain label columns")
        if "available_time" not in frame:
            raise ValueError("worker input requires available_time")
        if not isinstance(frame.available_time.dtype, pd.DatetimeTZDtype):
            raise ValueError("available_time must be timezone-aware")
        frame["available_time"] = frame.available_time.dt.tz_convert("UTC")
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "availability_columns", tuple(self.availability_columns))

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)

    def asof(self, decision_time: pd.Timestamp) -> pd.DataFrame:
        cutoff = pd.Timestamp(decision_time)
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("as-of decision time must be timezone-aware")
        cutoff = cutoff.tz_convert("UTC")
        if self._frame.empty or cutoff < self._frame.available_time.min():
            raise ValueError("as-of time is outside worker input range")
        result = self._frame.loc[self._frame.available_time <= cutoff].copy(deep=True)
        if "decision_time" in result:
            decisions = pd.to_datetime(result.decision_time, utc=True)
            result = result.loc[decisions <= cutoff]
        return result.reset_index(drop=True)


@dataclass(frozen=True)
class EvaluatorLabelsV1:
    _frame: pd.DataFrame = field(repr=False)
    decision_index: pd.Index | None = None

    def __post_init__(self):
        frame = _copy_frame(self._frame)
        if self.decision_index is not None and not frame.index.equals(self.decision_index):
            raise ValueError("evaluator labels do not align to decision index")
        object.__setattr__(self, "_frame", frame)
        if self.decision_index is not None:
            object.__setattr__(self, "decision_index", self.decision_index.copy())

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)


@dataclass(frozen=True)
class DatasetBuildReportV1:
    sources: tuple[dict, ...] = ()
    issues: tuple[dict, ...] = ()
    quality: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "sources", tuple(dict(item) for item in self.sources))
        object.__setattr__(self, "issues", tuple(dict(item) for item in self.issues))
        object.__setattr__(self, "quality", MappingProxyType(dict(self.quality)))


@dataclass(frozen=True)
class DatasetArtifactV1:
    worker_path: Path
    labels_path: Path
    report_path: Path
    manifest_path: Path
    fingerprints: Mapping[str, str]

    def __post_init__(self):
        object.__setattr__(self, "fingerprints", MappingProxyType(dict(self.fingerprints)))


@dataclass(frozen=True)
class DatasetBuildResultV2:
    worker_input: WorkerInputV2
    evaluator_labels: EvaluatorLabelsV1
    report: DatasetBuildReportV1
    manifest: dict
    artifact: DatasetArtifactV1 | None = None

    def __post_init__(self):
        object.__setattr__(self, "manifest", dict(self.manifest))


class UniverseDefinition(Protocol):
    @property
    def universe_hash(self) -> str: ...

    def membership(self, panel) -> pd.Series: ...


__all__ = [
    "DatasetArtifactV1", "DatasetBuildReportV1", "DatasetBuildResultV2",
    "EvaluatorLabelsV1", "UniverseDefinition", "WorkerInputV2", "asof_join",
]
