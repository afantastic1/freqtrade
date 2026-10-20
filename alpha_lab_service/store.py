"""Versioned append-only Store; intentionally independent of legacy Store code."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_ID = "alpha-lab-python-factor-store-v1"
STORE_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "user_data/research/alpha_lab/stores/python-factor-v1/experiments.sqlite3"
DEFAULT_PROTECTED = (
    ROOT / "user_data/research/alpha_lab/experiments.sqlite3",
    ROOT / "user_data/research/alpha_lab/stores/v1.1-cross-sectional/experiments.sqlite3",
)
IMMUTABLE_TABLES = ("store_metadata", "tasks", "task_events", "evidence", "decisions")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ResearchStoreV1:
    def __init__(self, path: Path = DEFAULT_PATH, *, protected_paths=DEFAULT_PROTECTED):
        requested = Path(path).expanduser()
        for component in (requested.absolute(), *requested.absolute().parents):
            if component.is_symlink():
                raise ValueError("Store path must not use symlinked components")
        normalized = requested.resolve(strict=False)
        for protected in map(Path, protected_paths):
            protected = protected.expanduser().resolve(strict=False)
            if normalized == protected:
                raise ValueError("Store path aliases a legacy database")
            if normalized.exists() and protected.exists() and normalized.samefile(protected):
                raise ValueError("Store path is a hardlink alias of a legacy database")
        self.path = normalized
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            os.chmod(self.path.parent, 0o700)
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise ValueError("Research Store directory permissions are too broad")
        if self.path.exists():
            self._validate_existing_identity()
            if stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                raise ValueError("existing Research Store permissions are too broad")
        else:
            self._create_schema()
            os.chmod(self.path, 0o600)
        self._validate_existing_identity()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _validate_existing_identity(self) -> None:
        try:
            with sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT key, value FROM store_metadata").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError("existing database has no recognized Store identity") from exc
        metadata = dict(rows)
        if metadata.get("store_id") != STORE_ID or metadata.get("store_version") != str(STORE_VERSION):
            raise ValueError("existing database Store identity mismatch")

    def _create_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE store_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE tasks(
                    task_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE task_events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    status TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE evidence(
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    evidence_hash TEXT NOT NULL UNIQUE,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE decisions(
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES evidence(run_id),
                    evidence_hash TEXT NOT NULL REFERENCES evidence(evidence_hash),
                    decision_hash TEXT NOT NULL UNIQUE,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_task_events_task ON task_events(task_id, event_id);
                CREATE INDEX idx_evidence_task ON evidence(task_id, created_at);
                CREATE INDEX idx_decisions_run ON decisions(run_id, created_at);
            """)
            now = _now()
            conn.executemany("INSERT INTO store_metadata VALUES (?, ?, ?)", [
                ("store_id", STORE_ID, now), ("store_version", str(STORE_VERSION), now),
                ("database_identity", "python-factor-v1", now),
            ])
            conn.execute(f"PRAGMA user_version={STORE_VERSION}")
            for table in IMMUTABLE_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    conn.execute(f"""
                        CREATE TRIGGER immutable_{table}_{operation.lower()}
                        BEFORE {operation} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'append-only table'); END
                    """)

    def append_task(self, *, task_id: str, request_hash: str, request: dict) -> str:
        request_json = _canonical(request)
        with self._connect() as conn:
            existing = conn.execute("SELECT task_id, request_json FROM tasks WHERE request_hash=?", (request_hash,)).fetchone()
            if existing:
                if existing["request_json"] != request_json:
                    raise ValueError("request hash conflict")
                if existing["task_id"] != task_id:
                    return str(existing["task_id"])
                return task_id
            try:
                conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)", (task_id, request_hash, request_json, _now()))
                conn.execute("INSERT INTO task_events(task_id,status,event_json,created_at) VALUES (?,?,?,?)",
                             (task_id, "SUBMITTED", "{}", _now()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("task identity conflict") from exc
        return task_id

    def append_task_status(self, task_id: str, status: str, event: dict | None = None) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO task_events(task_id,status,event_json,created_at) VALUES (?,?,?,?)",
                         (task_id, status, _canonical(event or {}), _now()))

    def append_evidence(self, *, task_id: str, run_id: str, evidence: dict) -> str:
        if evidence.get("status") not in {"EXPLORATORY", "DIAGNOSTIC"}:
            raise ValueError("Python factor Evidence must remain descriptive")
        payload = _canonical(evidence)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute("SELECT task_id,evidence_hash,evidence_json FROM evidence WHERE run_id=?", (run_id,)).fetchone()
            if row:
                if row["task_id"] == task_id and row["evidence_json"] == payload:
                    return str(row["evidence_hash"])
                raise ValueError("Evidence conflict for run identity")
            try:
                conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?)", (run_id, task_id, digest, payload, _now()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Evidence conflict or missing task") from exc
        return digest

    def append_decision(self, *, decision_id: str, run_id: str, decision: dict) -> str:
        if decision.get("status") not in {"EXPLORATORY", "DIAGNOSTIC"}:
            raise ValueError("Python factor Decision must remain descriptive")
        if set(decision) - {"status", "summary", "rationale"}:
            raise ValueError("Decision contains unsupported fields")
        if any(
            not isinstance(decision.get(key, ""), str) or len(decision.get(key, "")) > limit
            for key, limit in (("summary", 2000), ("rationale", 5000))
        ):
            raise ValueError("Decision text exceeds bounds")
        payload = _canonical(decision)
        decision_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            evidence = conn.execute("SELECT evidence_hash FROM evidence WHERE run_id=?", (run_id,)).fetchone()
            if evidence is None:
                raise sqlite3.IntegrityError("Decision requires an existing Evidence run")
            row = conn.execute("SELECT run_id,decision_json FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
            if row:
                if row["run_id"] == run_id and row["decision_json"] == payload:
                    return decision_hash
                raise ValueError("Decision identity conflict")
            conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                         (decision_id, run_id, evidence["evidence_hash"], decision_hash, payload, _now()))
        return decision_hash

    def get_task(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            event = conn.execute("SELECT status FROM task_events WHERE task_id=? ORDER BY event_id DESC LIMIT 1", (task_id,)).fetchone()
        return {"task_id": row["task_id"], "request_hash": row["request_hash"],
                "request": json.loads(row["request_json"]), "status": event["status"] if event else None}

    def get_evidence(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "task_id": row["task_id"],
                "evidence_hash": row["evidence_hash"], "evidence": json.loads(row["evidence_json"])}

    def list_history(self, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history limit outside allowed range")
        with self._connect() as conn:
            tasks = conn.execute("""
                SELECT task_id, request_hash,
                       json_extract(request_json, '$.hypothesis') AS hypothesis,
                       json_extract(request_json, '$.timeframe') AS timeframe,
                       json_extract(request_json, '$.start') AS start,
                       json_extract(request_json, '$.end') AS end, created_at
                FROM tasks ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            evidence_rows = conn.execute("""
                SELECT run_id, evidence_hash, json_extract(evidence_json, '$.status') AS status,
                       json_extract(evidence_json, '$.report.metrics') AS metrics, created_at
                FROM evidence ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            decision_rows = conn.execute("""
                SELECT decision_id, run_id, evidence_hash,
                       json_extract(decision_json, '$.status') AS status,
                       json_extract(decision_json, '$.summary') AS summary,
                       json_extract(decision_json, '$.rationale') AS rationale, created_at
                FROM decisions ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
        entries = []
        for row in tasks:
            entries.append({
                "kind": "TASK", "identity": row["task_id"], "created_at": row["created_at"],
                "payload": {
                    "request_hash": row["request_hash"],
                    "hypothesis": row["hypothesis"], "timeframe": row["timeframe"],
                    "start": row["start"], "end": row["end"],
                },
            })
        for row in evidence_rows:
            entries.append({
                "kind": "EVIDENCE", "identity": row["run_id"], "created_at": row["created_at"],
                "payload": {"evidence_hash": row["evidence_hash"], "status": row["status"],
                            "report": json.loads(row["metrics"]) if row["metrics"] else {}},
            })
        for row in decision_rows:
            entries.append({
                "kind": "DECISION", "identity": row["decision_id"], "created_at": row["created_at"],
                "payload": {"run_id": row["run_id"], "evidence_hash": row["evidence_hash"],
                            "decision": {"status": row["status"], "summary": row["summary"],
                                         "rationale": row["rationale"]}},
            })
        entries.sort(key=lambda entry: entry["created_at"], reverse=True)
        return [{key: value for key, value in entry.items() if key != "created_at"} for entry in entries[:limit]]

    def readonly_uri(self) -> str:
        return f"file:{self.path.as_posix()}?mode=ro"

    def integrity_check(self) -> str:
        with self._connect() as conn:
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])

    def foreign_key_check(self) -> list[tuple]:
        with self._connect() as conn:
            return [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
