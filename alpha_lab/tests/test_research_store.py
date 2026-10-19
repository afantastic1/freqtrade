import json
import sqlite3

import pytest

from alpha_lab.experiment_registry import ExperimentRegistry


def make_store(db_path):
    from alpha_lab import research_store
    return research_store.ResearchStore(db_path)


def completed_run(db_path, result_dir, run_id=None):
    registry = ExperimentRegistry(db_path)
    created = registry.create_run(config_hash="key", config_json="{}")
    if run_id is not None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE experiments SET run_id = ? WHERE run_id = ?",
                (run_id, created),
            )
        created = run_id
    registry.complete_run(created, result_dir)
    return created


def write_summary(
    run_dir,
    run_id,
    *,
    incremental=True,
    source_hash="a" * 64,
    legacy=False,
):
    run_dir.mkdir(parents=True, exist_ok=True)
    candidate = {
        "name": "signed_factor",
        "source_hash": source_hash,
        "source_path": "/agent/signed_factor/factor.py",
        "parameters": {"window": 12},
        "hypothesis": "Price location may precede reversal.",
    }
    inc = None
    if incremental:
        inc = {
            "factor_similarity": {
                "momentum_periods_4": {"spearman": -0.45},
            },
            "baseline": {"auc": 0.54},
            "candidate": {"auc": 0.53},
            "combined": {"auc": 0.541},
            "incremental": {
                "auc": 0.001,
                "logloss": 0.0004,
                "brier": -0.0001,
            },
            "stability": {
                "monthly": [
                    {"logloss_improvement": 0.1},
                    {"logloss_improvement": -0.1},
                ],
                "robust_positive": False,
            },
        }
    summary = {
        "run_id": run_id,
        "status": "completed",
        "candidate": candidate,
        "factor_evaluation": {
            "signed_factor_window_12": {
                "overall": {"rank_ic": -0.08, "ic": -0.06},
                "stability": {
                    "mean_monthly_rank_ic": -0.04,
                    "valid_months": 24,
                },
            }
        },
        "model_evaluation": {"model": {"auc": 0.53}},
        "incremental_evaluation": inc,
    }
    if legacy:
        summary["research_feedback"] = (
            {
                "classification": "INCONCLUSIVE",
                "tags": ["WEAK_INCREMENTAL_EVIDENCE"],
                "suggested_action": "INVESTIGATE_INSTABILITY",
            }
            if incremental else None
        )
    else:
        summary["evidence_report"] = {
            "schema_version": 1,
            "run_id": run_id,
            "candidate": candidate,
            "evaluation_context": {
                "evaluation_mode": (
                    "incremental" if incremental else "single_factor"
                ),
                "incremental_status": (
                    "evaluated" if incremental else "not_evaluated"
                ),
                "baseline": ({"factors": ["momentum_periods_4"]}
                             if incremental else None),
                "data": {"rows": 1000, "label_horizon": 4},
                "model": {"validation_protocol": "walk_forward"},
                "bootstrap": {
                    "samples": 1000,
                    "block_sizes": [24, 72, 168],
                    "status": "evaluated" if incremental else "not_evaluated",
                },
            },
            "factor_evaluation": summary["factor_evaluation"],
            "model_evaluation": summary["model_evaluation"],
            "incremental_evaluation": inc,
        }
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    return path


def test_fresh_schema_enforces_foreign_keys_and_indexes(tmp_path):
    db = tmp_path / "experiments.db"
    ExperimentRegistry(db)
    store = make_store(db)

    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"experiments", "candidates", "evaluations"} <= tables
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO evaluations (run_id, candidate_id, indexed_at) "
                "VALUES ('missing-run', 'missing-candidate', 'now')"
            )


def test_database_upgrade_keeps_old_rows_and_adds_columns(tmp_path):
    db = tmp_path / "experiments.db"
    ExperimentRegistry(db)
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE candidates (
                candidate_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO candidates VALUES (?, ?, ?, ?, ?)",
            ("legacy", "old_factor", "hash", "{}", "then"),
        )
        conn.execute("""
            CREATE TABLE evaluations (
                run_id TEXT PRIMARY KEY,
                candidate_id TEXT,
                auc REAL,
                indexed_at TEXT
            )
        """)

    make_store(db)

    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(evaluations)")
        }
        assert {
            "candidate_auc", "baseline_auc", "combined_auc", "evidence_json"
        } <= columns
        assert conn.execute(
            "SELECT name FROM candidates WHERE candidate_id='legacy'"
        ).fetchone()[0] == "old_factor"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO evaluations (run_id, candidate_id, indexed_at) "
                "VALUES ('orphan', 'legacy', 'now')"
            )


def test_research_store_idempotency_and_conflict(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "run-1"
    run_id = completed_run(db, run_dir, "run-1")
    summary = write_summary(run_dir, run_id)
    store = make_store(db)

    first = store.index_summary(run_id, summary)
    second = store.index_summary(run_id, summary)

    assert first["candidate_id"] == second["candidate_id"]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 1
        row = conn.execute(
            "SELECT classification, tags_json, feedback_json, evidence_json "
            "FROM evaluations WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row[:3] == (None, None, None)
        assert json.loads(row[3])["run_id"] == run_id

    changed = json.loads(summary.read_text())
    changed["candidate"]["source_hash"] = "b" * 64
    changed["evidence_report"]["candidate"]["source_hash"] = "b" * 64
    summary.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="different candidate_id"):
        store.index_summary(run_id, summary)


def test_indexes_signed_metrics_and_null_incremental_values(tmp_path):
    db = tmp_path / "experiments.db"
    inc_dir = tmp_path / "runs" / "incremental"
    single_dir = tmp_path / "runs" / "single"
    inc_id = completed_run(db, inc_dir, "incremental")
    single_id = completed_run(db, single_dir, "single")
    store = make_store(db)

    store.index_summary(inc_id, write_summary(inc_dir, inc_id))
    store.index_summary(
        single_id, write_summary(single_dir, single_id, incremental=False)
    )

    incremental = store.show_run(inc_id)
    assert incremental["rank_ic"] == pytest.approx(-0.08)
    assert incremental["ic"] == pytest.approx(-0.06)
    assert incremental["candidate_auc"] == pytest.approx(0.53)
    assert incremental["baseline_auc"] == pytest.approx(0.54)
    assert incremental["combined_auc"] == pytest.approx(0.541)
    assert incremental["max_abs_spearman"] == pytest.approx(0.45)
    assert incremental["positive_months"] == 1
    assert incremental["total_months"] == 2

    single = store.show_run(single_id)
    assert single["evaluation_mode"] == "single_factor"
    assert single["incremental_status"] == "not_evaluated"
    assert single["candidate_auc"] == pytest.approx(0.53)
    assert single["baseline_auc"] is None
    assert single["combined_auc"] is None
    assert single["delta_auc"] is None


def test_legacy_feedback_remains_readable_without_becoming_evidence(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "legacy"
    run_id = completed_run(db, run_dir, "legacy")
    store = make_store(db)

    store.index_summary(
        run_id, write_summary(run_dir, run_id, legacy=True)
    )

    with store.connect() as conn:
        row = conn.execute(
            "SELECT classification, tags_json, feedback_json, evidence_json "
            "FROM evaluations WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert row[0] == "INCONCLUSIVE"
    assert json.loads(row[1]) == ["WEAK_INCREMENTAL_EVIDENCE"]
    assert json.loads(row[2])["suggested_action"] == "INVESTIGATE_INSTABILITY"
    assert row[3] is None


def test_new_index_metrics_are_sourced_from_evidence_report(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "evidence-source"
    run_id = completed_run(db, run_dir, "evidence-source")
    summary = write_summary(run_dir, run_id)
    payload = json.loads(summary.read_text())
    payload["factor_evaluation"]["signed_factor_window_12"]["overall"] = {
        "rank_ic": 99.0,
        "ic": 99.0,
    }
    payload["model_evaluation"]["model"]["auc"] = 0.99
    payload["incremental_evaluation"]["incremental"]["auc"] = 0.99
    summary.write_text(json.dumps(payload), encoding="utf-8")

    indexed = make_store(db).index_summary(run_id, summary)

    assert indexed["rank_ic"] == pytest.approx(-0.08)
    assert indexed["ic"] == pytest.approx(-0.06)
    assert indexed["candidate_auc"] == pytest.approx(0.53)
    assert indexed["delta_auc"] == pytest.approx(0.001)


def test_list_runs_filters_candidate_and_status(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "listed"
    run_id = completed_run(db, run_dir, "listed")
    store = make_store(db)
    store.index_summary(run_id, write_summary(run_dir, run_id))

    assert [r["run_id"] for r in store.list_runs(candidate="signed_factor")] == [
        "listed"
    ]
    assert store.list_runs(candidate="other") == []
    assert [r["run_id"] for r in store.list_runs(status="completed")] == [
        "listed"
    ]


def test_new_snapshot_enriches_legacy_candidate_manifest(tmp_path):
    db = tmp_path / "experiments.db"
    old_dir = tmp_path / "runs" / "old"
    new_dir = tmp_path / "runs" / "new"
    old_id = completed_run(db, old_dir, "old")
    new_id = completed_run(db, new_dir, "new")
    store = make_store(db)
    store.index_summary(old_id, write_summary(old_dir, old_id, incremental=False))

    new_summary = write_summary(new_dir, new_id, incremental=False)
    payload = json.loads(new_summary.read_text())
    payload["candidate"]["required_columns"] = ["high", "low", "close"]
    payload["candidate"]["prediction_horizon"] = 4
    new_summary.write_text(json.dumps(payload), encoding="utf-8")
    store.index_summary(new_id, new_summary)

    candidate = store.show_run(new_id)
    manifest = json.loads(candidate["manifest_json"])
    assert manifest["required_columns"] == ["high", "low", "close"]
    assert manifest["prediction_horizon"] == 4


def _set_candidate_auc(payload, value):
    """Set candidate AUC on *every* copy the store may read.

    ``index_summary`` reads the incremental block from ``evidence_report``
    when it is present (see ``ResearchStore.index_summary``), while legacy
    summaries store it at the top level. Mutating only one copy leaves the
    payload internally inconsistent, which is exactly the state this test
    exists to forbid.
    """
    payload["incremental_evaluation"]["candidate"]["auc"] = value
    evidence = payload.get("evidence_report")
    if isinstance(evidence, dict):
        evidence["incremental_evaluation"]["candidate"]["auc"] = value


def _assert_consistent_identity_and_metrics(row, run_id):
    """The stored row must come entirely from one payload, never a mix."""
    source_hash = row["source_hash"]
    auc = row["candidate_auc"]
    assert (source_hash, auc) in {
        ("a" * 64, 0.53),
        ("b" * 64, 0.99),
    }, f"identity/metrics mixed: source_hash={source_hash!r} auc={auc!r} (run {run_id})"


def _conflicting_payloads(tmp_path, run_dir, run_id):
    first = write_summary(run_dir, run_id, source_hash="a" * 64)
    second = tmp_path / "other-summary.json"
    payload = json.loads(first.read_text())
    payload["candidate"]["source_hash"] = "b" * 64
    payload["evidence_report"]["candidate"]["source_hash"] = "b" * 64
    _set_candidate_auc(payload, 0.99)
    second.write_text(json.dumps(payload), encoding="utf-8")
    return first, second


def test_conflicting_index_cannot_mix_identity_and_metrics(tmp_path):
    """Deterministic version: no thread scheduling involved.

    Whichever payload is indexed first wins; the second must be rejected and
    must not overwrite any part of the winner.
    """
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "same"
    run_id = completed_run(db, run_dir, "same")
    first, second = _conflicting_payloads(tmp_path, run_dir, run_id)
    store = make_store(db)

    results = [
        _index_result(store, run_id, first),
        _index_result(store, run_id, second),
    ]

    assert results == ["ok", "conflict"]
    row = store.show_run(run_id)
    _assert_consistent_identity_and_metrics(row, run_id)
    assert row["source_hash"] == "a" * 64
    assert row["candidate_auc"] == 0.53


def test_concurrent_conflicting_index_cannot_mix_identity_and_metrics(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "same"
    run_id = completed_run(db, run_dir, "same")
    first, second = _conflicting_payloads(tmp_path, run_dir, run_id)
    store = make_store(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda path: _index_result(store, run_id, path),
            (first, second),
        ))

    assert sorted(results) == ["conflict", "ok"]
    _assert_consistent_identity_and_metrics(
        store.show_run(run_id), run_id
    )


def decision_payload(run_id="decision-run", source_hash="a" * 64):
    return {
        "run_id": run_id,
        "candidate_source_hash": source_hash,
        "supporting_evidence": [
            {"path": "incremental_evaluation.incremental.auc", "value": 0.001}
        ],
        "opposing_evidence": [
            {"path": "incremental_evaluation.stability.robust_positive",
             "value": False}
        ],
        "conclusion": "Evidence is mixed.",
        "next_hypothesis": {"text": "Test a less redundant transform."},
        "next_actions": ["create_candidate", "run_incremental_evaluation"],
        "agent_identity": "research-agent-test",
    }


def test_decisions_are_separate_append_only_records(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "decision-run"
    run_id = completed_run(db, run_dir, "decision-run")
    summary = write_summary(run_dir, run_id)
    store = make_store(db)
    store.index_summary(run_id, summary)
    before = summary.read_bytes()

    assert store.list_decisions(run_id) == []
    first = store.create_decision(**decision_payload())
    second_payload = decision_payload()
    second_payload["conclusion"] = {"status": "continue_research"}
    second = store.create_decision(**second_payload)

    decisions = store.list_decisions(run_id)
    assert len(decisions) == 2
    assert {item["decision_id"] for item in decisions} == {
        first["decision_id"], second["decision_id"]
    }
    assert decisions == sorted(
        decisions, key=lambda item: (item["created_at"], item["decision_id"])
    )
    assert decisions[0]["supporting_evidence"][0]["value"] == 0.001
    assert {str(item["conclusion"]) for item in decisions} == {
        "Evidence is mixed.", "{'status': 'continue_research'}"
    }
    assert summary.read_bytes() == before

    store.index_summary(run_id, summary)
    assert store.list_decisions(run_id) == decisions


@pytest.mark.parametrize(
    ("setup", "expected"),
    (
        ("missing", "Unknown run_id"),
        ("running", "not completed"),
        ("unindexed", "not indexed"),
    ),
)
def test_decision_requires_completed_indexed_run(tmp_path, setup, expected):
    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    if setup == "running":
        run_id = registry.create_run(config_hash="running", config_json="{}")
    elif setup == "unindexed":
        run_id = registry.create_run(config_hash="complete", config_json="{}")
        registry.complete_run(run_id, tmp_path / "runs" / run_id)
    else:
        run_id = "missing"
    store = make_store(db)
    payload = decision_payload(run_id=run_id)

    with pytest.raises(ValueError, match=expected):
        store.create_decision(**payload)


def test_decision_rejects_wrong_source_hash_and_invalid_json(tmp_path):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "decision-run"
    run_id = completed_run(db, run_dir, "decision-run")
    store = make_store(db)
    store.index_summary(run_id, write_summary(run_dir, run_id))

    wrong_hash = decision_payload(source_hash="b" * 64)
    with pytest.raises(ValueError, match="source_hash"):
        store.create_decision(**wrong_hash)

    not_json = decision_payload()
    not_json["supporting_evidence"] = {"bad": {1, 2}}
    with pytest.raises((TypeError, ValueError), match="JSON"):
        store.create_decision(**not_json)

    non_finite = decision_payload()
    non_finite["opposing_evidence"] = {"bad": float("nan")}
    with pytest.raises((TypeError, ValueError), match="JSON"):
        store.create_decision(**non_finite)

    assert store.list_decisions(run_id) == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("supporting_evidence", "not structured evidence"),
        ("opposing_evidence", 1),
        ("conclusion", ["not", "a conclusion object"]),
        ("next_hypothesis", ["not", "a hypothesis"]),
        ("next_actions", "not a structured action list"),
        ("agent_identity", {"not": "text"}),
    ),
)
def test_decision_rejects_payloads_with_wrong_structural_type(
    tmp_path, field, value
):
    db = tmp_path / "experiments.db"
    run_dir = tmp_path / "runs" / "decision-run"
    run_id = completed_run(db, run_dir, "decision-run")
    store = make_store(db)
    store.index_summary(run_id, write_summary(run_dir, run_id))
    payload = decision_payload()
    payload[field] = value

    with pytest.raises(TypeError, match=field):
        store.create_decision(**payload)

    assert store.list_decisions(run_id) == []


def _index_result(store, run_id, path):
    try:
        store.index_summary(run_id, path)
    except ValueError as exc:
        assert "different candidate_id" in str(exc)
        return "conflict"
    return "ok"
