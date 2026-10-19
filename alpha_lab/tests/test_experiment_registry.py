
import pytest
import sqlite3

from alpha_lab.experiment_registry import ExperimentRegistry


def test_create_and_complete_run(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")

    run_id = registry.create_run(
        config_hash="abc123",
        config_json='{"horizon":4}',
    )

    record = registry.get_run(run_id)

    assert record["status"] == "running"
    assert record["finished_at"] is None

    registry.complete_run(
        run_id,
        tmp_path / "runs" / run_id,
    )

    record = registry.get_run(run_id)

    assert record["status"] == "completed"
    assert record["finished_at"] is not None
    assert record["result_dir"] is not None


def test_same_config_creates_distinct_runs(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")

    first = registry.create_run(
        config_hash="same",
        config_json="{}",
    )
    second = registry.create_run(
        config_hash="same",
        config_json="{}",
    )

    assert first != second

    records = registry.find_by_config("same")
    assert len(records) == 2


def test_failed_run(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")

    run_id = registry.create_run(
        config_hash="failure",
        config_json="{}",
    )

    registry.fail_run(
        run_id,
        "ValueError: Invalid factor",
    )

    record = registry.get_run(run_id)

    assert record["status"] == "failed"
    assert "Invalid factor" in record["error"]


def test_completed_run_cannot_change(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")

    run_id = registry.create_run(
        config_hash="abc",
        config_json="{}",
    )

    registry.complete_run(run_id, tmp_path / "result")

    with pytest.raises(ValueError, match="already finished"):
        registry.fail_run(run_id, "Unexpected failure")


def test_database_persistence(tmp_path):
    db_path = tmp_path / "experiments.db"

    first_registry = ExperimentRegistry(db_path)
    run_id = first_registry.create_run(
        config_hash="persisted",
        config_json="{}",
    )

    # 模拟另一个进程重新打开 SQLite
    second_registry = ExperimentRegistry(db_path)

    assert second_registry.get_run(run_id)["status"] == "running"


def test_unknown_run(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")

    assert registry.get_run("missing") is None

    with pytest.raises(ValueError, match="Run not found"):
        registry.complete_run(
            "missing",
            tmp_path / "result",
        )


def test_experiment_identity_and_history_are_append_only(tmp_path):
    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="key", config_json="{}")
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable|state transition"):
            conn.execute("UPDATE experiments SET config_hash='other' WHERE run_id=?", (run_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM experiments WHERE run_id=?", (run_id,))
    registry.complete_run(run_id, tmp_path / "result")
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="state transition"):
            conn.execute("UPDATE experiments SET status='running' WHERE run_id=?", (run_id,))
