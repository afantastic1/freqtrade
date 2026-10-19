
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


VALID_STATUSES = {"running", "completed", "failed"}


class CompletedRunAvailable(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(
            parents=True, exist_ok=True
        )
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self):
        if self.db_path.is_file() and self.db_path.stat().st_size:
            with sqlite3.connect(self.db_path) as current:
                tables = {row[0] for row in current.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                triggers = {row[0] for row in current.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'")}
                expected = {"experiments_immutable", "experiments_status_transition",
                            "experiments_no_delete"}
                if "experiments" in tables and not expected.issubset(triggers):
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    backup_path = self.db_path.with_name(f"{self.db_path.name}.backup-{stamp}")
                    with sqlite3.connect(backup_path) as backup:
                        current.backup(backup)
                        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise RuntimeError("database backup integrity check failed")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    run_id TEXT PRIMARY KEY,
                    config_hash TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN (
                            'running', 'completed', 'failed'
                        )),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    config_json TEXT NOT NULL,
                    code_version TEXT,
                    data_fingerprint TEXT,
                    result_dir TEXT,
                    error TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_experiments_config_hash
                ON experiments(config_hash)
            """)
            conn.execute("""CREATE TRIGGER IF NOT EXISTS experiments_immutable
                BEFORE UPDATE ON experiments
                WHEN NEW.run_id != OLD.run_id OR NEW.config_hash != OLD.config_hash
                  OR NEW.started_at != OLD.started_at OR NEW.config_json != OLD.config_json
                  OR COALESCE(NEW.code_version,'') != COALESCE(OLD.code_version,'')
                  OR COALESCE(NEW.data_fingerprint,'') != COALESCE(OLD.data_fingerprint,'')
                BEGIN SELECT RAISE(ABORT, 'experiment identity is immutable'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS experiments_status_transition
                BEFORE UPDATE ON experiments
                WHEN NOT (OLD.status='running' AND NEW.status IN ('completed','failed')
                          AND NEW.finished_at IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'invalid experiment state transition'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS experiments_no_delete
                BEFORE DELETE ON experiments
                BEGIN SELECT RAISE(ABORT, 'experiments are append-only'); END""")

    def create_run(
        self,
        *,
        config_hash: str,
        config_json: str,
        code_version: str | None = None,
        data_fingerprint: str | None = None,
        run_id: str | None = None,
    ) -> str:
        run_id = run_id or uuid.uuid4().hex

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO experiments (
                    run_id,
                    config_hash,
                    status,
                    started_at,
                    config_json,
                    code_version,
                    data_fingerprint
                )
                VALUES (?, ?, 'running', ?, ?, ?, ?)
            """, (
                run_id,
                config_hash,
                utc_now(),
                config_json,
                code_version,
                data_fingerprint,
            ))

        return run_id

    def claim_run(
        self,
        *,
        config_hash: str,
        config_json: str,
        code_version: str | None = None,
        data_fingerprint: str | None = None,
        stale_after: timedelta = timedelta(hours=6),
        ignored_completed_run_ids: tuple[str, ...] = (),
        allow_completed: bool = False,
    ) -> str:
        """Atomically claim a research key, recovering abandoned claims."""
        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not allow_completed:
                completed = conn.execute("""
                    SELECT run_id FROM experiments
                    WHERE config_hash = ? AND status = 'completed'
                    ORDER BY started_at DESC
                """, (config_hash,)).fetchall()
                ignored = set(ignored_completed_run_ids)
                available = next(
                    (row["run_id"] for row in completed if row["run_id"] not in ignored),
                    None,
                )
                if available is not None:
                    raise CompletedRunAvailable(
                        f"Completed research run available: {available}"
                    )
            running = conn.execute("""
                SELECT run_id, started_at
                FROM experiments
                WHERE config_hash = ? AND status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
            """, (config_hash,)).fetchone()
            if running is not None:
                started = datetime.fromisoformat(running["started_at"])
                if now - started <= stale_after:
                    raise RuntimeError(
                        f"Research key already running: {running['run_id']}"
                    )
                conn.execute("""
                    UPDATE experiments
                    SET status = 'failed', finished_at = ?,
                        error = 'Stale running claim recovered'
                    WHERE run_id = ? AND status = 'running'
                """, (now.isoformat(), running["run_id"]))
            conn.execute("""
                INSERT INTO experiments (
                    run_id, config_hash, status, started_at, config_json,
                    code_version, data_fingerprint
                ) VALUES (?, ?, 'running', ?, ?, ?, ?)
            """, (
                run_id, config_hash, now.isoformat(), config_json,
                code_version, data_fingerprint,
            ))
        return run_id

    def complete_run(
        self,
        run_id: str,
        result_dir: str | Path,
    ):
        self._finish_run(
            run_id=run_id,
            status="completed",
            result_dir=str(result_dir),
            error=None,
        )

    def fail_run(
        self,
        run_id: str,
        error: str,
    ):
        self._finish_run(
            run_id=run_id,
            status="failed",
            result_dir=None,
            error=error,
        )

    def _finish_run(
        self,
        *,
        run_id: str,
        status: str,
        result_dir: str | None,
        error: str | None,
    ):
        if status not in {"completed", "failed"}:
            raise ValueError("Invalid final status")

        with self._connect() as conn:
            cursor = conn.execute("""
                UPDATE experiments
                SET status = ?,
                    finished_at = ?,
                    result_dir = COALESCE(?, result_dir),
                    error = ?
                WHERE run_id = ?
                  AND status = 'running'
            """, (
                status,
                utc_now(),
                result_dir,
                error,
                run_id,
            ))

            if cursor.rowcount != 1:
                raise ValueError(
                    f"Run not found or already finished: {run_id}"
                )

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT *
                FROM experiments
                WHERE run_id = ?
            """, (run_id,)).fetchone()

        return dict(row) if row is not None else None

    def find_by_config(
        self,
        config_hash: str,
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT *
                FROM experiments
                WHERE config_hash = ?
                ORDER BY started_at DESC, run_id DESC
            """, (config_hash,)).fetchall()

        return [dict(row) for row in rows]
