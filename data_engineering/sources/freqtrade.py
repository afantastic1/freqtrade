"""Read-only adapter for local Freqtrade Feather history files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Mapping

import pandas as pd

from ..data_kind_registry import DATA_KIND_CONTRACTS
from ..freqtrade_feather import (
    _canonicalize_frame,
    _parse_filename,
    _safe_root,
    _safe_source_path,
    _source_digest,
    _validate_columns,
)
from .contracts import (
    AdapterCapability,
    AvailabilityStatus,
    DatasetRequest,
    DownloadResult,
    DownloadStatus,
    ObservationBatch,
    SourceArtifact,
    UnsupportedCapabilityError,
)


def _month_intervals(start: datetime, end: datetime):
    cursor = pd.Timestamp(start).tz_convert("UTC").normalize().replace(day=1)
    request_end = pd.Timestamp(end).tz_convert("UTC")
    while cursor < request_end:
        next_month = cursor + pd.offsets.MonthBegin(1)
        bounded_start = max(cursor, pd.Timestamp(start))
        bounded_end = min(next_month, request_end)
        if bounded_start < bounded_end:
            yield bounded_start.to_pydatetime(), bounded_end.to_pydatetime()
        cursor = next_month


class FreqtradeFeatherAdapter:
    """Discover and read explicitly scoped local Feather datasets without repair."""

    name = "freqtrade-feather"
    capabilities = frozenset({AdapterCapability.DISCOVER, AdapterCapability.READ})

    def __init__(
        self,
        source_roots: Mapping[str, Path],
        *,
        exchange: str,
        market_type: str,
        event_cadence: str | None = None,
    ) -> None:
        if not source_roots:
            raise ValueError("at least one configured source root is required")
        if not exchange or not market_type:
            raise ValueError("exchange and market_type are required")
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(key)) for key in source_roots):
            raise ValueError("invalid source root ID")
        self.source_roots = {str(key): Path(value) for key, value in source_roots.items()}
        self.exchange = exchange
        self.market_type = market_type
        self.event_cadence = event_cadence

    def discover(self, request: DatasetRequest) -> list[SourceArtifact]:
        """Return one bounded artifact for each observed month in matching files."""
        if (request.identity.exchange, request.identity.market_type) != (
            self.exchange, self.market_type
        ):
            raise ValueError("request scope does not match configured exchange and market type")
        artifacts: list[SourceArtifact] = []
        for root_id, configured_root in sorted(self.source_roots.items()):
            root = _safe_root(configured_root)
            for path in sorted(root.rglob("*.feather"), key=lambda item: item.as_posix()):
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise ValueError("source symlink forbidden")
                try:
                    parsed = _parse_filename(
                        path.name, exchange=self.exchange, market_type=self.market_type
                    )
                except ValueError:
                    continue
                if (
                    parsed["exchange"], parsed["market_type"], parsed["asset"],
                    parsed["exchange_symbol"], parsed["data_kind"], parsed["timeframe"],
                ) != (
                    request.identity.exchange, request.identity.market_type,
                    request.identity.asset, request.identity.exchange_symbol,
                    request.identity.data_kind, request.identity.timeframe,
                ):
                    continue
                source_path, relative_path = _safe_source_path(root, relative)
                frame = _canonicalize_frame(pd.read_feather(source_path), request.identity.data_kind)
                _validate_columns(frame, request.identity.data_kind)
                timestamp_field = DATA_KIND_CONTRACTS[request.identity.data_kind]["timestamp_field"]
                timestamps = pd.to_datetime(frame[timestamp_field], errors="raise")
                if timestamps.dt.tz is None:
                    raise ValueError("timestamp must have an explicit UTC timezone")
                timestamps = timestamps.dt.tz_convert("UTC")
                if ((timestamps < request.end) & (timestamps >= request.start)).sum() == 0:
                    continue
                for start, end in _month_intervals(request.start, request.end):
                    if not ((timestamps >= start) & (timestamps < end)).any():
                        continue
                    artifacts.append(
                        SourceArtifact(
                            identity=request.identity,
                            request_id=request.request_id,
                            source_locator=f"{root_id}:{relative_path}",
                            source_format="feather",
                            start=start,
                            end=end,
                            availability=AvailabilityStatus.AVAILABLE,
                            local_path=source_path,
                            source_root_id=root_id,
                            relative_path=relative_path,
                            metadata=(
                                {"event_cadence": self.event_cadence}
                                if self.event_cadence is not None else {}
                            ),
                        )
                    )
        return artifacts

    def read(self, artifact: SourceArtifact) -> ObservationBatch:
        """Read one request/month slice, preserving source ordering and duplicates."""
        if artifact.identity.data_kind not in DATA_KIND_CONTRACTS:
            raise ValueError("unsupported local data kind")
        root_id = artifact.source_root_id
        relative_path = artifact.relative_path
        if root_id is None or relative_path is None or root_id not in self.source_roots:
            raise ValueError("artifact must identify a configured source root and relative path")
        root = _safe_root(self.source_roots[root_id])
        source_path, normalized_relative = _safe_source_path(root, Path(relative_path))
        if normalized_relative != relative_path:
            raise ValueError("artifact source path is not canonical")
        parsed = _parse_filename(
            source_path.name, exchange=self.exchange, market_type=self.market_type
        )
        expected = (
            artifact.identity.exchange, artifact.identity.market_type, artifact.identity.asset,
            artifact.identity.exchange_symbol, artifact.identity.data_kind,
            artifact.identity.timeframe,
        )
        observed = (
            parsed["exchange"], parsed["market_type"], parsed["asset"],
            parsed["exchange_symbol"], parsed["data_kind"], parsed["timeframe"],
        )
        if observed != expected:
            raise ValueError("artifact identity does not match its source filename")
        frame = _canonicalize_frame(pd.read_feather(source_path), artifact.identity.data_kind)
        _validate_columns(frame, artifact.identity.data_kind)
        timestamp_field = DATA_KIND_CONTRACTS[artifact.identity.data_kind]["timestamp_field"]
        timestamps = pd.to_datetime(frame[timestamp_field], errors="raise")
        if timestamps.dt.tz is None:
            raise ValueError("timestamp must have an explicit UTC timezone")
        timestamps = timestamps.dt.tz_convert("UTC")
        frame[timestamp_field] = timestamps
        mask = (timestamps >= artifact.start) & (timestamps < artifact.end)
        selected = frame.loc[mask].copy()
        if selected.empty:
            raise ValueError("source artifact has no observations in its declared bounds")
        selected.reset_index(drop=True, inplace=True)
        return ObservationBatch(
            identity=artifact.identity,
            artifact=artifact,
            observations=selected,
            source_sha256=_source_digest(source_path),
            metadata={
                "adapter": self.name,
                "source_root_id": root_id,
                "relative_path": relative_path,
                "source_format": "feather",
            },
        )

    def download(self, artifact: SourceArtifact, destination: Path) -> DownloadResult:
        del artifact, destination
        raise UnsupportedCapabilityError("local Feather sources do not support download")
