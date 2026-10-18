from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_resolve_db_path_rejects_path_outside_research_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from registry import resolve_db_path

    with pytest.raises(ValueError, match="user_data/research"):
        resolve_db_path("outside.sqlite")


def test_migrate_is_idempotent_and_enables_guards(tmp_path):
    from registry import connect, migrate, registry_summary

    connection = connect(tmp_path / "alpha.sqlite")
    version = migrate(connection)
    # 迁移是顺序编号的（1..N），因此版本号等于已应用迁移的行数。
    assert version >= 2
    assert migrate(connection) == version  # 幂等：重复调用不再应用任何迁移
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert registry_summary(connection)["schema_version"] == version
    assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == version


def test_short_cycle_migration_adds_columns_and_tradability_table(tmp_path):
    from registry import connect, migrate

    connection = connect(tmp_path / "alpha.sqlite")
    migrate(connection)

    factor_cols = {row[1] for row in connection.execute("PRAGMA table_info(factors)")}
    experiment_cols = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
    hypothesis_cols = {row[1] for row in connection.execute("PRAGMA table_info(hypotheses)")}

    assert {"timeframe"} <= factor_cols
    assert {"timeframe", "horizon_duration", "capital_usdt"} <= experiment_cols
    assert {"frequency"} <= hypothesis_cols
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "tradability" in tables


def test_legacy_registry_migration_and_raw_preservation(tmp_path):
    """迁移 003：legacy_registry 表存在，可归档原始记录且按旧 id 幂等。"""
    from registry import connect, migrate
    import registry_ops as OPS

    connection = connect(tmp_path / "alpha.sqlite")
    assert migrate(connection) >= 3
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "legacy_registry" in tables
    cols = {row[1] for row in connection.execute("PRAGMA table_info(legacy_registry)")}
    assert {"id", "source", "shape", "raw_json", "mapped_factor_id"} <= cols
    # 原始记录逐字保留（含无法结构化映射的策略回测）
    raw = {"id": "E001", "strategy": "BreakoutA", "metrics": {"n": 11, "sharpe": -0.24}}
    r1 = OPS.record_legacy(connection, dict(id="E001", source="registry.jsonl",
                                            shape="ab12", alpha_type=None, raw=raw))
    r2 = OPS.record_legacy(connection, dict(id="E001", source="registry.jsonl",
                                            shape="ab12", alpha_type=None, raw=raw))
    assert r1["status"] == "inserted" and r2["status"] == "already_exists"
    stored = connection.execute(
        "SELECT raw_json FROM legacy_registry WHERE id='E001'").fetchone()[0]
    import json as _json
    assert _json.loads(stored) == raw
    assert connection.execute("SELECT count(*) FROM legacy_registry").fetchone()[0] == 1
    with pytest.raises(ValueError, match="raw must be a JSON object"):
        OPS.record_legacy(connection, dict(id="E002", raw="not-a-dict"))


def test_v1_payload_still_writes_after_short_cycle_migration(tmp_path):
    """向后兼容：迁移后旧（v1）payload 仍可写入，新列留空。"""
    from catalog import import_samples
    from registry import connect, migrate

    connection = connect(tmp_path / "alpha.sqlite")
    migrate(connection)
    # catalog 的内置样例即为 v1 结构（不含 timeframe/horizon/capital）
    result = import_samples(connection, dry_run=False)
    assert result["inserted"] == 2
    row = connection.execute(
        "SELECT timeframe FROM factors WHERE id = 'F-SAMPLE-MOM-024'").fetchone()
    assert row["timeframe"] is None


def test_sample_import_is_atomic_and_idempotent(tmp_path):
    from catalog import import_samples
    from registry import connect, migrate

    connection = connect(tmp_path / "alpha.sqlite")
    migrate(connection)
    first = import_samples(connection, dry_run=False)
    second = import_samples(connection, dry_run=False)

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["existing"] == 2
    assert connection.execute("SELECT count(*) FROM hypotheses").fetchone()[0] == 2
    assert connection.execute("SELECT count(*) FROM factors").fetchone()[0] == 2
