import json
import sqlite3
import stat

import pytest

from alpha_lab_service.store_v2 import ResearchStoreV2


def new_store(tmp_path):
    return ResearchStoreV2(tmp_path / "python-factor-v2" / "experiments.sqlite3", protected_paths=())


def create_task(store, *, count=1, stateful=False):
    chunks = [
        {"chunk_id": f"chunk-{index}", "chunk_index": index, "input_hash": str(index + 1) * 64}
        for index in range(count)
    ]
    task_id = store.create_or_get_task(
        task_id="task-1", request_hash="a" * 64,
        request={"execution_mode": "stateful" if stateful else "stateless", "source": "private"},
        plan_hash="b" * 64, chunks=chunks,
    )
    return task_id, chunks


def start_task(store):
    store.append_task_status("task-1", "RUNNING")


def commit_chunk(store, chunk_id="chunk-0", *, attempt=1, input_hash="1" * 64, state_in=None, state_out=None):
    store.begin_chunk_attempt("task-1", chunk_id, input_hash=input_hash, state_in=state_in)
    return store.commit_chunk(
        "task-1", chunk_id, attempt, output=[{"as_of": "2025-01-01T00:05:00Z", "value": 1.0}],
        state_out=state_out,
    )


def test_store_v2_has_private_directory_and_database_identity(tmp_path):
    store = new_store(tmp_path)
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert dict(conn.execute("SELECT key,value FROM store_metadata"))["store_id"] == "alpha-lab-python-factor-store-v2"


def test_store_v2_rejects_symlink_alias_and_wrong_existing_identity(tmp_path):
    protected = tmp_path / "legacy.sqlite3"
    sqlite3.connect(protected).close()
    alias = tmp_path / "hardlink.sqlite3"
    alias.hardlink_to(protected)
    with pytest.raises(ValueError, match="alias"):
        ResearchStoreV2(alias, protected_paths=(protected,))
    link = tmp_path / "store-link.sqlite3"
    link.symlink_to(protected)
    with pytest.raises(ValueError, match="symlink"):
        ResearchStoreV2(link, protected_paths=())
    with pytest.raises(ValueError, match="identity"):
        ResearchStoreV2(protected, protected_paths=())


def test_task_creation_is_idempotent_and_binds_chunk_plan(tmp_path):
    store = new_store(tmp_path)
    task_id, chunks = create_task(store, count=2)
    assert store.create_or_get_task(
        task_id="other-task", request_hash="a" * 64,
        request={"execution_mode": "stateless", "source": "private"},
        plan_hash="b" * 64, chunks=chunks,
    ) == task_id
    with pytest.raises(ValueError, match="conflict"):
        store.create_or_get_task(
            task_id=task_id, request_hash="a" * 64,
            request={"execution_mode": "stateless", "source": "changed"},
            plan_hash="b" * 64, chunks=chunks,
        )
    assert store.get_task(task_id)["status"] == "QUEUED"


def test_task_transitions_are_monotonic_and_terminal_states_cannot_resume(tmp_path):
    store = new_store(tmp_path)
    create_task(store)
    start_task(store)
    store.append_task_status("task-1", "RETRYABLE_FAILURE", {"category": "runtime"})
    store.append_task_status("task-1", "RUNNING")
    store.append_task_status("task-1", "FAILED")
    with pytest.raises(ValueError, match="transition"):
        store.append_task_status("task-1", "RUNNING")


def test_retry_resumes_from_last_committed_checkpoint_only(tmp_path):
    store = new_store(tmp_path)
    create_task(store, count=2, stateful=True)
    start_task(store)
    first_attempt = store.begin_chunk_attempt("task-1", "chunk-0", input_hash="1" * 64, state_in=None)
    store.commit_chunk(
        "task-1", "chunk-0", first_attempt,
        output=[{"as_of": "2025-01-01T00:05:00Z", "value": 1.0}],
        state_out={"count": 1},
    )
    assert store.get_resume_state("task-1", "chunk-1") == {"count": 1}
    failed = store.begin_chunk_attempt("task-1", "chunk-1", input_hash="2" * 64, state_in={"count": 1})
    store.record_chunk_failure("task-1", "chunk-1", failed, retryable=True, diagnostics={"category": "timeout"})
    assert store.get_resume_state("task-1", "chunk-1") == {"count": 1}
    store.append_task_status("task-1", "RUNNING")
    retried = store.begin_chunk_attempt("task-1", "chunk-1", input_hash="2" * 64, state_in={"count": 1})
    assert retried == 2
    store.commit_chunk(
        "task-1", "chunk-1", retried,
        output=[{"as_of": "2025-01-01T00:10:00Z", "value": 2.0}],
        state_out={"count": 2},
    )
    assert store.get_resume_state("task-1", "chunk-1") == {"count": 1}


def test_committed_chunk_is_immutable_and_checkpoint_hashes_are_persisted(tmp_path):
    store = new_store(tmp_path)
    create_task(store, stateful=True)
    start_task(store)
    digest = commit_chunk(store, state_out={"count": 1})
    assert len(digest) == 64
    chunk = store.get_chunk("task-1", "chunk-0")
    assert chunk["status"] == "COMMITTED"
    assert chunk["state_out_hash"] and chunk["output_hash"]
    with pytest.raises(ValueError, match="immutable"):
        store.commit_chunk(
            "task-1", "chunk-0", 1,
            output=[{"as_of": "changed", "value": 2.0}], state_out={"count": 1},
        )
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE chunk_commits SET output_json='{}' WHERE task_id='task-1'")


def test_evidence_and_completed_transition_commit_atomically(tmp_path):
    store = new_store(tmp_path)
    create_task(store)
    start_task(store)
    commit_chunk(store)
    digest = store.complete_with_evidence(
        task_id="task-1", run_id="run-1", evidence={"status": "DIAGNOSTIC", "report": {"mean": 0.1}}
    )
    assert len(digest) == 64
    assert store.get_task("task-1")["status"] == "COMPLETED"
    assert store.get_evidence("run-1")["evidence_hash"] == digest


def test_evidence_publication_rolls_back_when_completion_event_fails(tmp_path):
    store = new_store(tmp_path)
    create_task(store)
    start_task(store)
    commit_chunk(store)
    with store._connect() as conn:
        conn.execute("""
            CREATE TRIGGER fail_completed_event BEFORE INSERT ON task_events
            WHEN NEW.status='COMPLETED'
            BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.complete_with_evidence(
            task_id="task-1", run_id="run-1", evidence={"status": "DIAGNOSTIC"}
        )
    assert store.get_evidence("run-1") is None
    assert store.get_task("task-1")["status"] == "RUNNING"


def test_incomplete_or_terminal_task_cannot_publish_evidence(tmp_path):
    store = new_store(tmp_path)
    create_task(store, count=2)
    start_task(store)
    commit_chunk(store)
    with pytest.raises(ValueError, match="all planned chunks"):
        store.complete_with_evidence(task_id="task-1", run_id="run", evidence={"status": "DIAGNOSTIC"})
    store.append_task_status("task-1", "FAILED")
    with pytest.raises(ValueError, match="terminal"):
        store.complete_with_evidence(task_id="task-1", run_id="run", evidence={"status": "DIAGNOSTIC"})
    assert store.get_evidence("run") is None
