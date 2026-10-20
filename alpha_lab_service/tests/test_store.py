import hashlib
import json
import sqlite3
import stat

import pytest

from alpha_lab_service.store import ResearchStoreV1, STORE_ID


def test_store_creates_versioned_identity_and_readonly_history(tmp_path):
    db = tmp_path / "python-factor-v1" / "experiments.sqlite3"
    store = ResearchStoreV1(db)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    task_id = store.append_task(task_id="task-1", request_hash="req-1", request={"source_sha256": "abc"})
    assert task_id == "task-1"
    assert store.get_task(task_id)["request_hash"] == "req-1"
    assert store.readonly_uri().startswith("file:")
    with sqlite3.connect(store.readonly_uri(), uri=True) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM tasks")
    assert store.integrity_check() == "ok"


def test_store_refuses_legacy_paths_aliases_and_unrelated_databases(tmp_path):
    with pytest.raises(ValueError, match="legacy"):
        ResearchStoreV1(tmp_path / "experiments.sqlite3", protected_paths=[tmp_path / "experiments.sqlite3"])
    protected = tmp_path / "historical.sqlite3"
    sqlite3.connect(protected).close()
    hardlink = tmp_path / "alias.sqlite3"
    hardlink.hardlink_to(protected)
    with pytest.raises(ValueError, match="alias"):
        ResearchStoreV1(hardlink, protected_paths=[protected])
    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(protected)
    with pytest.raises(ValueError, match="symlink"):
        ResearchStoreV1(symlink, protected_paths=[protected])
    with pytest.raises(ValueError, match="identity"):
        ResearchStoreV1(protected)


def test_store_refuses_world_accessible_parent_directory(tmp_path):
    directory = tmp_path / "public-store-dir"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="directory permissions"):
        ResearchStoreV1(directory / "experiments.sqlite3")


def test_store_refuses_symlinked_parent_paths(tmp_path):
    real_dir = tmp_path / "real-store-dir"
    real_dir.mkdir(mode=0o700)
    alias = tmp_path / "store-alias"
    alias.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ResearchStoreV1(alias / "experiments.sqlite3")


def test_append_idempotency_conflicts_and_decision_foreign_key(tmp_path):
    store = ResearchStoreV1(tmp_path / "store.sqlite3")
    store.append_task(task_id="t", request_hash="h", request={"x": 1})
    assert store.append_task(task_id="t", request_hash="h", request={"x": 1}) == "t"
    store.append_evidence(task_id="t", run_id="r", evidence={"metric": 1, "status": "DIAGNOSTIC"})
    store.append_evidence(task_id="t", run_id="r", evidence={"metric": 1, "status": "DIAGNOSTIC"})
    with pytest.raises(ValueError, match="conflict"):
        store.append_evidence(task_id="t", run_id="r", evidence={"metric": 2, "status": "DIAGNOSTIC"})
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision(decision_id="d0", run_id="missing", decision={"status": "DIAGNOSTIC"})
    store.append_decision(decision_id="d1", run_id="r", decision={"status": "EXPLORATORY"})
    store.append_decision(decision_id="d1", run_id="r", decision={"status": "EXPLORATORY"})
    assert len(store.list_history()) == 3


def test_immutable_triggers_reject_updates_and_deletes_on_disposable_copy(tmp_path):
    store = ResearchStoreV1(tmp_path / "store.sqlite3")
    store.append_task(task_id="t", request_hash="h", request={})
    store.append_evidence(task_id="t", run_id="r", evidence={"status": "DIAGNOSTIC"})
    store.append_decision(decision_id="d", run_id="r", decision={"status": "DIAGNOSTIC"})
    disposable = tmp_path / "copy.sqlite3"
    disposable.write_bytes((tmp_path / "store.sqlite3").read_bytes())
    with sqlite3.connect(disposable) as conn:
        for table in ("tasks", "task_events", "evidence", "decisions"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE {table} SET created_at='changed'")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"DELETE FROM {table}")
    assert store.integrity_check() == "ok"
    assert store.foreign_key_check() == []
    assert hashlib.sha256(disposable.read_bytes()).digest() != hashlib.sha256(b"").digest()


def test_store_refuses_formal_supported_status_even_when_called_directly(tmp_path):
    store = ResearchStoreV1(tmp_path / "store.sqlite3")
    store.append_task(task_id="t", request_hash="h", request={})
    with pytest.raises(ValueError, match="descriptive"):
        store.append_evidence(task_id="t", run_id="r", evidence={"status": "SUPPORTED"})
    store.append_evidence(task_id="t", run_id="r", evidence={"status": "DIAGNOSTIC"})
    with pytest.raises(ValueError, match="descriptive"):
        store.append_decision(decision_id="d", run_id="r", decision={"status": "SUPPORTED"})
    with pytest.raises(ValueError, match="fields"):
        store.append_decision(
            decision_id="d2", run_id="r", decision={"status": "DIAGNOSTIC", "p_value": 0.01}
        )


def test_history_query_returns_bounded_summaries_without_source_or_factor_rows(tmp_path):
    store = ResearchStoreV1(tmp_path / "store.sqlite3")
    store.append_task(task_id="t", request_hash="h", request={"source": "secret source", "hypothesis": "test"})
    store.append_evidence(task_id="t", run_id="r", evidence={
        "status": "DIAGNOSTIC", "factor_values": [{"symbol": "AAA", "value": 99}],
        "report": {"metrics": {"mean_cs_ic": 0.1}},
    })
    history = store.list_history()
    assert "secret source" not in json.dumps(history)
    assert "factor_values" not in json.dumps(history)
    with pytest.raises(ValueError, match="limit"):
        store.list_history(limit=101)
