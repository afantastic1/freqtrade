"""Trusted append-only registry for Campaign-authorized ResearchRequest v2."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ResearchRequestRegistryV2:
    """Validate requests against trusted Campaign policies before immutable storage."""

    def __init__(self, path: str | Path, policy_registry: CampaignPolicyRegistry):
        supplied_path = Path(path).expanduser()
        if supplied_path.is_symlink():
            raise ValueError("research request registry may not be a symlink")
        self.path = supplied_path.absolute()
        self.policy_registry = policy_registry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = f"{self.path.as_uri()}?mode=ro"
            return sqlite3.connect(uri, uri=True, timeout=10)
        return sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")

    def _initialize(self) -> None:
        if self.path.exists() and not self.path.is_file():
            raise ValueError("research request registry path must be a file")
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_requests_v2 (
                    request_id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    canonical_request TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS research_requests_v2_no_update
                BEFORE UPDATE ON research_requests_v2
                BEGIN SELECT RAISE(ABORT, 'immutable research request'); END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS research_requests_v2_no_delete
                BEFORE DELETE ON research_requests_v2
                BEGIN SELECT RAISE(ABORT, 'immutable research request'); END
            """)
            conn.commit()

    def create(self, payload: Mapping[str, object]) -> dict[str, Any]:
        """Authorize and append a request, returning an identical replay unchanged."""
        if not isinstance(payload, Mapping):
            raise ValueError("research request must be an object")
        request = ResearchRequestV2.from_dict(payload, self.policy_registry)
        canonical = request.canonical_json()
        digest = request.sha256()
        encoded = json.loads(canonical)

        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT request_sha256, canonical_request FROM research_requests_v2 WHERE request_id = ?",
                    (request.request_id,),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO research_requests_v2 (request_id, request_sha256, canonical_request) VALUES (?, ?, ?)",
                        (request.request_id, digest, canonical),
                    )
                elif row != (digest, canonical):
                    raise ValueError("request ID already exists with different content")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return encoded

    def get(self, research_request_id: str) -> dict[str, Any]:
        """Read one canonical request without opening the database for writes."""
        if not isinstance(research_request_id, str) or not _REQUEST_ID.fullmatch(research_request_id):
            raise ValueError("unknown research request")
        try:
            with closing(self._connect(readonly=True)) as conn:
                row = conn.execute(
                    "SELECT request_sha256, canonical_request FROM research_requests_v2 WHERE request_id = ?",
                    (research_request_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("research request registry unavailable") from exc
        if row is None:
            raise ValueError("unknown research request")
        digest, canonical = row
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != digest:
            raise ValueError("stored research request digest mismatch")
        record = json.loads(canonical)
        if record.get("request_id") != research_request_id:
            raise ValueError("stored research request identity mismatch")
        return record
