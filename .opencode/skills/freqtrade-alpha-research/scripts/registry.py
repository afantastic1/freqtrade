from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB = Path("user_data/research/alpha.sqlite")
MIGRATIONS = Path(__file__).with_name("migrations")


def resolve_db_path(raw: str | None = None) -> Path:
    root = (Path.cwd() / "user_data" / "research").resolve()
    candidate = (Path(raw) if raw else DEFAULT_DB).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"database path must be inside {root} (user_data/research)")
    return candidate


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def migrate(connection: sqlite3.Connection) -> int:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(migration.name.split("_", 1)[0])
        if version in applied:
            continue
        script = migration.read_text(encoding="utf-8")
        statements = [statement.strip() for statement in script.split(";") if statement.strip()]
        with connection:
            for statement in statements:
                if not (version == 1 and statement.startswith("CREATE TABLE schema_migrations")):
                    connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def registry_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    version = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    counts = {}
    for table in ("hypotheses", "factors", "experiments", "evaluations", "research_runs"):
        counts[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return {"schema_version": int(version), "counts": counts}
