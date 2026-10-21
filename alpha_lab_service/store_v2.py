"""Private, versioned task/chunk/checkpoint Store for Python Factor Service v2."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from alpha_lab_service.protocol_v2 import V2_LIMITS

STORE_ID = "alpha-lab-python-factor-store-v2"
STORE_VERSION = 2
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "user_data/research/alpha_lab/stores/python-factor-v2/experiments.sqlite3"
DEFAULT_PROTECTED = (
    ROOT / "user_data/research/alpha_lab/experiments.sqlite3",
    ROOT / "user_data/research/alpha_lab/stores/v1.1-cross-sectional/experiments.sqlite3",
    ROOT / "user_data/research/alpha_lab/stores/python-factor-v1/experiments.sqlite3",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TERMINAL = {"COMPLETED", "FAILED", "EXECUTION_BLOCKED", "CANCELLED"}
_TRANSITIONS = {
    "QUEUED": {"RUNNING", "FAILED", "EXECUTION_BLOCKED", "CANCELLED"},
    "RUNNING": {"RUNNING", "RETRYABLE_FAILURE", "COMPLETED", "FAILED", "EXECUTION_BLOCKED", "CANCELLED"},
    "RETRYABLE_FAILURE": {"RUNNING", "FAILED", "EXECUTION_BLOCKED", "CANCELLED"},
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_state(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise ValueError("checkpoint state must be a JSON object")
    encoded = _canonical(value)
    if len(encoded.encode("utf-8")) > V2_LIMITS.max_state_bytes:
        raise ValueError("checkpoint state exceeds byte limit")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ResearchStoreV2:
    def __init__(self, path: Path = DEFAULT_PATH, *, protected_paths: Sequence[Path] = DEFAULT_PROTECTED):
        requested = Path(path).expanduser()
        absolute = requested.absolute()
        for component in (absolute, *absolute.parents):
            if component.is_symlink():
                raise ValueError("Store path must not use symlinked components")
        normalized = absolute.resolve(strict=False)
        for protected in map(lambda value: Path(value).expanduser().resolve(strict=False), protected_paths):
            if normalized == protected:
                raise ValueError("Store path aliases a protected database")
            if normalized.exists() and protected.exists() and normalized.samefile(protected):
                raise ValueError("Store path is a hardlink alias of a protected database")
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
        conn = sqlite3.connect(self.path, timeout=10, isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _validate_existing_identity(self) -> None:
        try:
            with sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True) as conn:
                metadata = dict(conn.execute("SELECT key,value FROM store_metadata").fetchall())
        except sqlite3.DatabaseError as exc:
            raise ValueError("existing database has no recognized Store identity") from exc
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
                    plan_hash TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE task_events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    status TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE chunks(
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    chunk_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    plan_hash TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, chunk_id),
                    UNIQUE(task_id, chunk_index)
                );
                CREATE TABLE chunk_attempts(
                    task_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    state_in_json TEXT,
                    state_in_hash TEXT,
                    started_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, chunk_id, attempt_no),
                    FOREIGN KEY(task_id, chunk_id) REFERENCES chunks(task_id, chunk_id)
                );
                CREATE TABLE chunk_attempt_events(
                    attempt_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id, chunk_id, attempt_no)
                        REFERENCES chunk_attempts(task_id, chunk_id, attempt_no)
                );
                CREATE TABLE chunk_commits(
                    task_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    output_json TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    state_out_json TEXT,
                    state_out_hash TEXT,
                    committed_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, chunk_id),
                    FOREIGN KEY(task_id, chunk_id, attempt_no)
                        REFERENCES chunk_attempts(task_id, chunk_id, attempt_no)
                );
                CREATE TABLE evidence(
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                    evidence_hash TEXT NOT NULL UNIQUE,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_task_events_latest ON task_events(task_id,event_id);
                CREATE INDEX idx_chunks_order ON chunks(task_id,chunk_index);
                CREATE INDEX idx_chunk_attempts ON chunk_attempts(task_id,chunk_id,attempt_no);
                CREATE INDEX idx_chunk_attempt_events ON chunk_attempt_events(task_id,chunk_id,attempt_no,attempt_event_id);
            """)
            now = _now()
            conn.executemany("INSERT INTO store_metadata VALUES (?,?,?)", [
                ("store_id", STORE_ID, now), ("store_version", str(STORE_VERSION), now),
                ("database_identity", "python-factor-v2", now),
            ])
            conn.execute(f"PRAGMA user_version={STORE_VERSION}")
            for table in ("store_metadata", "tasks", "task_events", "chunks", "chunk_attempts",
                          "chunk_attempt_events", "chunk_commits", "evidence"):
                for operation in ("UPDATE", "DELETE"):
                    conn.execute(f"""
                        CREATE TRIGGER immutable_{table}_{operation.lower()}
                        BEFORE {operation} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'append-only table'); END
                    """)

    @staticmethod
    def _latest_status(conn: sqlite3.Connection, task_id: str) -> str | None:
        row = conn.execute(
            "SELECT status FROM task_events WHERE task_id=? ORDER BY event_id DESC LIMIT 1", (task_id,)
        ).fetchone()
        return row["status"] if row else None

    def _append_task_event(self, conn: sqlite3.Connection, task_id: str, status: str, event: dict | None):
        current = self._latest_status(conn, task_id)
        allowed = {"QUEUED"} if current is None else _TRANSITIONS.get(current, set())
        if current in _TERMINAL or status not in allowed:
            raise ValueError(f"invalid task status transition {current} -> {status}")
        payload = _canonical(event or {})
        if len(payload.encode("utf-8")) > 4096:
            raise ValueError("task event metadata exceeds byte limit")
        conn.execute(
            "INSERT INTO task_events(task_id,status,event_json,created_at) VALUES (?,?,?,?)",
            (task_id, status, payload, _now()),
        )

    def create_or_get_task(self, *, task_id: str, request_hash: str, request: dict,
                           plan_hash: str, chunks: Sequence[dict]) -> str:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("invalid task id")
        if not _SHA256.fullmatch(request_hash) or not _SHA256.fullmatch(plan_hash):
            raise ValueError("task and chunk plan identities must be SHA-256 values")
        if not isinstance(request, dict) or not chunks or len(chunks) > V2_LIMITS.max_total_steps:
            raise ValueError("request and non-empty bounded chunks are required")
        request_json = _canonical(request)
        if len(request_json.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("canonical task request exceeds byte limit")
        normalized_chunks = []
        seen_ids = set()
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict) or set(chunk) != {"chunk_id", "chunk_index", "input_hash"}:
                raise ValueError("chunk plan entries must contain only identity and input hash")
            if (chunk["chunk_index"] != index or not isinstance(chunk["chunk_id"], str)
                    or not _TASK_ID.fullmatch(chunk["chunk_id"]) or chunk["chunk_id"] in seen_ids
                    or not _SHA256.fullmatch(chunk["input_hash"])):
                raise ValueError("invalid or unordered chunk plan entry")
            seen_ids.add(chunk["chunk_id"])
            normalized_chunks.append(chunk)
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM tasks WHERE request_hash=?", (request_hash,)).fetchone()
            if existing:
                existing_chunks = conn.execute(
                    "SELECT chunk_id,chunk_index,input_hash FROM chunks WHERE task_id=? ORDER BY chunk_index",
                    (existing["task_id"],),
                ).fetchall()
                expected = [(item["chunk_id"], item["chunk_index"], item["input_hash"]) for item in normalized_chunks]
                actual = [(row["chunk_id"], row["chunk_index"], row["input_hash"]) for row in existing_chunks]
                if (existing["request_json"], existing["plan_hash"], actual) != (request_json, plan_hash, expected):
                    raise ValueError("task identity conflict")
                return str(existing["task_id"])
            try:
                conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                             (task_id, request_hash, request_json, plan_hash, len(chunks), _now()))
                self._append_task_event(conn, task_id, "QUEUED", {"plan_hash": plan_hash})
                conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", [
                    (task_id, item["chunk_id"], item["chunk_index"], plan_hash, item["input_hash"], _now())
                    for item in normalized_chunks
                ])
            except sqlite3.IntegrityError as exc:
                raise ValueError("task identity conflict") from exc
        return task_id

    def append_task_status(self, task_id: str, status: str, event: dict | None = None) -> None:
        with self._connect() as conn:
            self._append_task_event(conn, task_id, status, event)

    def begin_chunk_attempt(self, task_id: str, chunk_id: str, *, input_hash: str, state_in: dict | None,
                            input_bytes: int = 0) -> int:
        if not _SHA256.fullmatch(input_hash):
            raise ValueError("chunk input hash must be a SHA-256 value")
        if type(input_bytes) is not int or input_bytes < 0 or input_bytes > V2_LIMITS.max_input_bytes_per_chunk:
            raise ValueError("chunk input byte count is outside the service limit")
        state_json, state_hash = _validate_state(state_in)
        with self._connect() as conn:
            if self._latest_status(conn, task_id) != "RUNNING":
                raise ValueError("task must be RUNNING before a chunk attempt")
            prior_input = 0
            for event_row in conn.execute(
                "SELECT e.event_json FROM chunk_attempt_events e JOIN ("
                "SELECT chunk_id,attempt_no,MAX(attempt_event_id) event_id FROM chunk_attempt_events "
                "WHERE task_id=? GROUP BY chunk_id,attempt_no) latest ON e.attempt_event_id=latest.event_id",
                (task_id,),
            ).fetchall():
                prior_input += int(json.loads(event_row["event_json"]).get("input_bytes", 0))
            if prior_input + input_bytes > V2_LIMITS.max_total_input_bytes:
                raise ValueError("cumulative Worker input exceeds service limit")
            chunk = conn.execute(
                "SELECT chunk_index,input_hash FROM chunks WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)
            ).fetchone()
            if chunk is None:
                raise ValueError("unknown chunk")
            if chunk["input_hash"] != input_hash:
                raise ValueError("chunk input identity mismatch")
            if conn.execute("SELECT 1 FROM chunk_commits WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)).fetchone():
                raise ValueError("committed chunk is immutable and cannot be retried")
            if chunk["chunk_index"] == 0:
                if state_in is not None:
                    raise ValueError("first chunk cannot receive a prior checkpoint")
            else:
                previous = conn.execute(
                    "SELECT chunk_id FROM chunks WHERE task_id=? AND chunk_index=?",
                    (task_id, chunk["chunk_index"] - 1),
                ).fetchone()
                commit = conn.execute(
                    "SELECT state_out_json,state_out_hash FROM chunk_commits WHERE task_id=? AND chunk_id=?",
                    (task_id, previous["chunk_id"] if previous else ""),
                ).fetchone()
                if commit is None or commit["state_out_json"] != state_json or commit["state_out_hash"] != state_hash:
                    raise ValueError("chunk state input must match the preceding committed checkpoint")
            last = conn.execute(
                "SELECT attempt_no FROM chunk_attempts WHERE task_id=? AND chunk_id=? ORDER BY attempt_no DESC LIMIT 1",
                (task_id, chunk_id),
            ).fetchone()
            attempt_no = int(last["attempt_no"]) + 1 if last else 1
            if attempt_no > V2_LIMITS.max_attempts_per_chunk:
                raise ValueError("chunk retry limit exceeded")
            if last:
                event = conn.execute(
                    "SELECT status FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? ORDER BY attempt_event_id DESC LIMIT 1",
                    (task_id, chunk_id, last["attempt_no"]),
                ).fetchone()
                if event is None or event["status"] != "RETRYABLE_FAILURE":
                    raise ValueError("only a retryable incomplete chunk may be attempted again")
                prior = conn.execute(
                    "SELECT input_hash,state_in_json,state_in_hash FROM chunk_attempts WHERE task_id=? AND chunk_id=? AND attempt_no=?",
                    (task_id, chunk_id, last["attempt_no"]),
                ).fetchone()
                if (prior["input_hash"], prior["state_in_json"], prior["state_in_hash"]) != (input_hash, state_json, state_hash):
                    raise ValueError("retry must use identical chunk input and checkpoint")
                prior_bytes = json.loads(conn.execute(
                    "SELECT event_json FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? "
                    "ORDER BY attempt_event_id LIMIT 1", (task_id, chunk_id, last["attempt_no"]),
                ).fetchone()["event_json"]).get("input_bytes", 0)
                if prior_bytes != input_bytes:
                    raise ValueError("retry must use identical serialized input size")
            conn.execute("INSERT INTO chunk_attempts VALUES (?,?,?,?,?,?,?)",
                         (task_id, chunk_id, attempt_no, input_hash, state_json, state_hash, _now()))
            conn.execute("INSERT INTO chunk_attempt_events(task_id,chunk_id,attempt_no,status,event_json,created_at) VALUES (?,?,?,?,?,?)",
                         (task_id, chunk_id, attempt_no, "RUNNING", _canonical({"input_bytes": input_bytes}), _now()))
        return attempt_no

    def recover_interrupted_attempts(self, task_id: str) -> int:
        """Close orphaned RUNNING attempts after process loss so they can be replayed."""
        recovered = 0
        with self._connect() as conn:
            if self._latest_status(conn, task_id) != "RUNNING":
                return 0
            rows = conn.execute(
                "SELECT a.chunk_id,a.attempt_no,a.started_at FROM chunk_attempts a "
                "JOIN (SELECT chunk_id,MAX(attempt_no) attempt_no FROM chunk_attempts "
                "WHERE task_id=? GROUP BY chunk_id) latest "
                "ON a.chunk_id=latest.chunk_id AND a.attempt_no=latest.attempt_no "
                "WHERE a.task_id=?", (task_id, task_id),
            ).fetchall()
            now = datetime.now(timezone.utc)
            for row in rows:
                if conn.execute(
                    "SELECT 1 FROM chunk_commits WHERE task_id=? AND chunk_id=?",
                    (task_id, row["chunk_id"]),
                ).fetchone():
                    continue
                latest_event = conn.execute(
                    "SELECT status FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? "
                    "ORDER BY attempt_event_id DESC LIMIT 1",
                    (task_id, row["chunk_id"], row["attempt_no"]),
                ).fetchone()
                if not latest_event or latest_event["status"] != "RUNNING":
                    continue
                started = datetime.fromisoformat(row["started_at"])
                elapsed = max(0.0, (now - started).total_seconds())
                initial = conn.execute(
                    "SELECT event_json FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? "
                    "ORDER BY attempt_event_id LIMIT 1",
                    (task_id, row["chunk_id"], row["attempt_no"]),
                ).fetchone()
                input_bytes = json.loads(initial["event_json"]).get("input_bytes", 0)
                event = _canonical({"category": "interrupted", "elapsed_seconds": elapsed,
                                    "input_bytes": input_bytes})
                conn.execute(
                    "INSERT INTO chunk_attempt_events(task_id,chunk_id,attempt_no,status,event_json,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (task_id, row["chunk_id"], row["attempt_no"], "RETRYABLE_FAILURE", event, _now()),
                )
                self._append_task_event(conn, task_id, "RETRYABLE_FAILURE", {
                    "chunk_id": row["chunk_id"], "attempt": row["attempt_no"], "category": "interrupted",
                })
                recovered += 1
        return recovered

    def record_chunk_failure(self, task_id: str, chunk_id: str, attempt_no: int, *, retryable: bool,
                             diagnostics: dict | None = None, elapsed_seconds: float = 0,
                             blocked: bool = False) -> str:
        if not isinstance(elapsed_seconds, (int, float)) or elapsed_seconds < 0:
            raise ValueError("elapsed Worker time must be a non-negative number")
        payload_obj = dict(diagnostics or {})
        payload_obj["elapsed_seconds"] = float(elapsed_seconds)
        payload = _canonical(payload_obj)
        with self._connect() as conn:
            attempt = conn.execute(
                "SELECT input_hash FROM chunk_attempts WHERE task_id=? AND chunk_id=? AND attempt_no=?",
                (task_id, chunk_id, attempt_no),
            ).fetchone()
            started_event = conn.execute(
                "SELECT event_json FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? "
                "ORDER BY attempt_event_id LIMIT 1", (task_id, chunk_id, attempt_no),
            ).fetchone()
            latest = conn.execute(
                "SELECT status FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? ORDER BY attempt_event_id DESC LIMIT 1",
                (task_id, chunk_id, attempt_no),
            ).fetchone()
            if not attempt or not latest or latest["status"] != "RUNNING":
                raise ValueError("only a running chunk attempt may fail")
            payload_obj["input_bytes"] = json.loads(started_event["event_json"]).get("input_bytes", 0)
            payload = _canonical(payload_obj)
            if len(payload.encode("utf-8")) > 4096:
                raise ValueError("chunk diagnostics exceed byte limit")
            status = ("EXECUTION_BLOCKED" if blocked else
                      "RETRYABLE_FAILURE" if retryable and attempt_no < V2_LIMITS.max_attempts_per_chunk else "FAILED")
            conn.execute("INSERT INTO chunk_attempt_events(task_id,chunk_id,attempt_no,status,event_json,created_at) VALUES (?,?,?,?,?,?)",
                         (task_id, chunk_id, attempt_no, status, payload, _now()))
            task_status = "EXECUTION_BLOCKED" if blocked else status
            self._append_task_event(conn, task_id, task_status, {"chunk_id": chunk_id, "attempt": attempt_no})
        return status

    def commit_chunk(self, task_id: str, chunk_id: str, attempt_no: int, *, output: list,
                     state_out: dict | None, elapsed_seconds: float = 0) -> str:
        if not isinstance(elapsed_seconds, (int, float)) or elapsed_seconds < 0:
            raise ValueError("elapsed Worker time must be a non-negative number")
        output_json = _canonical(output)
        output_bytes = len(output_json.encode("utf-8"))
        if output_bytes > V2_LIMITS.max_output_bytes_per_chunk:
            raise ValueError("chunk output exceeds byte limit")
        state_json, state_hash = _validate_state(state_out)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT output_json,output_hash,state_out_json,state_out_hash FROM chunk_commits WHERE task_id=? AND chunk_id=?",
                (task_id, chunk_id),
            ).fetchone()
            if existing:
                output_hash = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
                if (existing["output_json"], existing["state_out_json"], existing["state_out_hash"]) == (output_json, state_json, state_hash):
                    return str(existing["output_hash"])
                raise ValueError("committed chunk is immutable")
            if self._latest_status(conn, task_id) != "RUNNING":
                raise ValueError("task must be RUNNING to commit a chunk")
            attempt = conn.execute(
                "SELECT input_hash,state_in_json,state_in_hash FROM chunk_attempts WHERE task_id=? AND chunk_id=? AND attempt_no=?",
                (task_id, chunk_id, attempt_no),
            ).fetchone()
            latest = conn.execute(
                "SELECT status FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? AND attempt_no=? ORDER BY attempt_event_id DESC LIMIT 1",
                (task_id, chunk_id, attempt_no),
            ).fetchone()
            if not attempt or not latest or latest["status"] != "RUNNING":
                raise ValueError("chunk commit requires the active attempt")
            task = conn.execute("SELECT request_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            stored_request = json.loads(task["request_json"])
            factor_request = stored_request.get("factor_request", stored_request)
            stateful = factor_request.get("execution_mode") == "stateful"
            if stateful != (state_out is not None):
                raise ValueError("chunk state output does not match execution mode")
            total_output = conn.execute(
                "SELECT COALESCE(SUM(length(output_json)+length(COALESCE(state_out_json,''))),0) AS total "
                "FROM chunk_commits WHERE task_id=?", (task_id,)
            ).fetchone()["total"]
            if total_output + output_bytes + len(state_json.encode("utf-8") if state_json is not None else b"") > V2_LIMITS.max_total_output_bytes:
                raise ValueError("cumulative task output exceeds byte limit")
            output_hash = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
            conn.execute("INSERT INTO chunk_commits VALUES (?,?,?,?,?,?,?,?)", (
                task_id, chunk_id, attempt_no, output_json, output_hash, state_json, state_hash, _now(),
            ))
            event = _canonical({"output_hash": output_hash, "state_in_hash": attempt["state_in_hash"],
                                "state_out_hash": state_hash, "elapsed_seconds": float(elapsed_seconds),
                                "input_bytes": json.loads(conn.execute(
                                    "SELECT event_json FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? "
                                    "AND attempt_no=? ORDER BY attempt_event_id LIMIT 1",
                                    (task_id, chunk_id, attempt_no),
                                ).fetchone()["event_json"]).get("input_bytes", 0)})
            conn.execute("INSERT INTO chunk_attempt_events(task_id,chunk_id,attempt_no,status,event_json,created_at) VALUES (?,?,?,?,?,?)",
                         (task_id, chunk_id, attempt_no, "COMMITTED", event, _now()))
            self._append_task_event(conn, task_id, "RUNNING", {"chunk_id": chunk_id, "status": "COMMITTED"})
        return output_hash

    def get_resume_state(self, task_id: str, chunk_id: str) -> dict | None:
        with self._connect() as conn:
            chunk = conn.execute("SELECT chunk_index FROM chunks WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)).fetchone()
            if chunk is None:
                raise ValueError("unknown chunk")
            if chunk["chunk_index"] == 0:
                return None
            previous = conn.execute(
                "SELECT chunk_id FROM chunks WHERE task_id=? AND chunk_index=?",
                (task_id, chunk["chunk_index"] - 1),
            ).fetchone()
            commit = conn.execute(
                "SELECT state_out_json FROM chunk_commits WHERE task_id=? AND chunk_id=?",
                (task_id, previous["chunk_id"] if previous else ""),
            ).fetchone()
            if commit is None:
                raise ValueError("preceding chunk is not committed")
        return json.loads(commit["state_out_json"]) if commit["state_out_json"] is not None else None

    def get_chunk(self, task_id: str, chunk_id: str) -> dict | None:
        with self._connect() as conn:
            chunk = conn.execute("SELECT * FROM chunks WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)).fetchone()
            if chunk is None:
                return None
            commit = conn.execute(
                "SELECT output_hash,state_out_hash,attempt_no FROM chunk_commits WHERE task_id=? AND chunk_id=?",
                (task_id, chunk_id),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM chunk_attempts WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)
            ).fetchone()["n"]
            latest = conn.execute(
                "SELECT status FROM chunk_attempt_events WHERE task_id=? AND chunk_id=? ORDER BY attempt_event_id DESC LIMIT 1",
                (task_id, chunk_id),
            ).fetchone()
        return {
            "chunk_id": chunk["chunk_id"], "chunk_index": chunk["chunk_index"],
            "plan_hash": chunk["plan_hash"], "input_hash": chunk["input_hash"],
            "status": "COMMITTED" if commit else (latest["status"] if latest else "QUEUED"),
            "attempts": count, "output_hash": commit["output_hash"] if commit else None,
            "state_out_hash": commit["state_out_hash"] if commit else None,
        }

    def get_chunks(self, task_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chunk_id,chunk_index,plan_hash,input_hash FROM chunks WHERE task_id=? ORDER BY chunk_index",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_committed_output(self, task_id: str, chunk_id: str) -> list:
        with self._connect() as conn:
            row = conn.execute("SELECT output_json FROM chunk_commits WHERE task_id=? AND chunk_id=?", (task_id, chunk_id)).fetchone()
        if row is None:
            raise ValueError("chunk is not committed")
        return json.loads(row["output_json"])

    def get_task(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            task = conn.execute("SELECT task_id,request_hash,plan_hash,chunk_count,created_at FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                return None
            status = self._latest_status(conn, task_id)
        return {**dict(task), "status": status}

    def get_total_worker_wall_seconds(self, task_id: str) -> float:
        with self._connect() as conn:
            events = conn.execute(
                "SELECT event_json FROM chunk_attempt_events WHERE task_id=? AND status IN "
                "('COMMITTED','RETRYABLE_FAILURE','FAILED','EXECUTION_BLOCKED')", (task_id,)
            ).fetchall()
        return sum(float(json.loads(row["event_json"]).get("elapsed_seconds", 0)) for row in events)

    def get_total_worker_input_bytes(self, task_id: str) -> int:
        with self._connect() as conn:
            events = conn.execute(
                "SELECT e.event_json FROM chunk_attempt_events e JOIN ("
                "SELECT chunk_id,attempt_no,MAX(attempt_event_id) event_id FROM chunk_attempt_events "
                "WHERE task_id=? GROUP BY chunk_id,attempt_no) latest ON e.attempt_event_id=latest.event_id",
                (task_id,),
            ).fetchall()
        return sum(int(json.loads(row["event_json"]).get("input_bytes", 0)) for row in events)

    def get_task_record(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "request": json.loads(row["request_json"])}

    def get_evidence(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "task_id": row["task_id"], "evidence_hash": row["evidence_hash"],
                "evidence": json.loads(row["evidence_json"])}

    def complete_with_evidence(self, *, task_id: str, run_id: str, evidence: dict) -> str:
        if evidence.get("status") not in {"EXPLORATORY", "DIAGNOSTIC"}:
            raise ValueError("v2 Evidence must remain descriptive")
        evidence_json = _canonical(evidence)
        if len(evidence_json.encode("utf-8")) > V2_LIMITS.max_total_output_bytes + 16 * 1024 * 1024:
            raise ValueError("Evidence exceeds byte limit")
        evidence_hash = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM evidence WHERE run_id=?", (run_id,)).fetchone()
            status = self._latest_status(conn, task_id)
            if existing:
                if (existing["task_id"], existing["evidence_json"]) == (task_id, evidence_json) and status == "COMPLETED":
                    return str(existing["evidence_hash"])
                raise ValueError("Evidence identity conflict")
            if status in _TERMINAL:
                raise ValueError("terminal task cannot publish Evidence")
            if status != "RUNNING":
                raise ValueError("task must be RUNNING to publish Evidence")
            task = conn.execute("SELECT chunk_count FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            committed = conn.execute("SELECT COUNT(*) AS n FROM chunk_commits WHERE task_id=?", (task_id,)).fetchone()["n"]
            if task is None or committed != task["chunk_count"]:
                raise ValueError("all planned chunks must be committed before Evidence publication")
            conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?)", (run_id, task_id, evidence_hash, evidence_json, _now()))
            self._append_task_event(conn, task_id, "COMPLETED", {"evidence_hash": evidence_hash, "run_id": run_id})
        return evidence_hash
