"""Atomic per-artifact stage journal for resumable source preparation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from ..catalog_v3 import _reject_symlink_components


class JournalState(StrEnum):
    DISCOVERED = "DISCOVERED"
    DOWNLOADED = "DOWNLOADED"
    VALIDATED = "VALIDATED"
    OBJECT_PUBLISHED = "OBJECT_PUBLISHED"
    CATALOG_REGISTERED = "CATALOG_REGISTERED"
    UNAVAILABLE = "UNAVAILABLE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    CHECKSUM_FAILED = "CHECKSUM_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


_ORDER = {
    JournalState.DISCOVERED: 0,
    JournalState.DOWNLOADED: 1,
    JournalState.VALIDATED: 2,
    JournalState.OBJECT_PUBLISHED: 3,
    JournalState.CATALOG_REGISTERED: 4,
}
_FAILURES = {
    JournalState.UNAVAILABLE, JournalState.DOWNLOAD_FAILED, JournalState.CHECKSUM_FAILED,
    JournalState.PARSE_FAILED, JournalState.QUALITY_FAILED, JournalState.PUBLISH_FAILED,
}


class JournalStore:
    """Persist one latest stage record per stable request/artifact key."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, artifact_key: str) -> Path:
        if not isinstance(artifact_key, str) or not artifact_key:
            raise ValueError("artifact_key must be a non-empty string")
        digest = hashlib.sha256(artifact_key.encode("utf-8")).hexdigest()
        return _reject_symlink_components(
            self.root / f"{digest}.json", description="source journal"
        )

    def read(self, artifact_key: str) -> dict[str, Any] | None:
        path = self._path(artifact_key)
        if path.is_symlink():
            raise ValueError("source journal symlink forbidden")
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("artifact_key_sha256") != hashlib.sha256(artifact_key.encode()).hexdigest():
            raise ValueError("source journal key mismatch")
        return record

    def transition(
        self,
        artifact_key: str,
        state: JournalState,
        *,
        request_id: str,
        dataset_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = JournalState(state)
        path = self._path(artifact_key)
        current = self.read(artifact_key)
        if current:
            if (current["request_id"], current["dataset_id"]) != (request_id, dataset_id):
                raise ValueError("journal request or dataset identity cannot change")
            previous = JournalState(current["status"])
            if previous in _FAILURES and state is not previous:
                raise ValueError("terminal journal failure cannot transition")
            if state not in _FAILURES and _ORDER.get(state, -1) < _ORDER.get(previous, -1):
                raise ValueError("journal stage transition cannot move backward")
            if state not in _FAILURES and previous in _FAILURES:
                raise ValueError("terminal journal failure cannot transition")
        record = {
            "artifact_key_sha256": hashlib.sha256(artifact_key.encode()).hexdigest(),
            "request_id": request_id,
            "dataset_id": dataset_id,
            "status": state.value,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "details": dict(details or {}),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(path, description="source journal")
        fd, temp_name = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return record
