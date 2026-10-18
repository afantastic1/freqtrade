
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


VALID_STATUSES = {"running", "completed", "failed"}


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
        return conn

    def _initialize(self):
        with self._connect() as conn:
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

    def create_run(
        self,
        *,
        config_hash: str,
        config_json: str,
        code_version: str | None = None,
        data_fingerprint: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex

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
