import json

from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore


def add_parent(db, run_id, run_dir):
    registry = ExperimentRegistry(db)
    created = registry.create_run(config_hash=run_id, config_json="{}", run_id=run_id)
    registry.complete_run(run_id, run_dir)


def summary(run_id, *, incremental=False):
    inc = None
    feedback = None
    if incremental:
        inc = {
            "factor_similarity": {"mom": {"spearman": -0.4}},
            "baseline": {"auc": 0.54},
            "candidate": {"auc": 0.53},
            "combined": {"auc": 0.541},
            "incremental": {"auc": 0.001, "logloss": 0.002, "brier": 0.001},
            "stability": {"monthly": [], "robust_positive": False},
        }
        feedback = {"classification": "INCONCLUSIVE", "tags": []}
    return {
        "run_id": run_id,
        "status": "completed",
        "candidate": {
            "name": "old_candidate",
            "source_hash": "a" * 64,
            "parameters": {"window": 12},
            "hypothesis": "Historical candidate.",
        },
        "factor_evaluation": {
            "old_candidate_window_12": {
                "overall": {"rank_ic": -0.08, "ic": -0.06},
                "stability": {"valid_months": 24, "mean_monthly_rank_ic": -0.04},
            }
        },
        "model_evaluation": {"model": {"auc": 0.53}},
        "incremental_evaluation": inc,
        "research_feedback": feedback,
    }


def write_run(root, run_id, payload):
    folder = root / run_id
    folder.mkdir(parents=True)
    path = folder / "summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_backfill_old_single_and_incremental_is_idempotent(tmp_path):
    from alpha_lab.research_cli import backfill
    db = tmp_path / "experiments.db"
    runs = tmp_path / "runs"
    for run_id, incremental in (("single", False), ("incremental", True)):
        run_dir = runs / run_id
        add_parent(db, run_id, run_dir)
        write_run(runs, run_id, summary(run_id, incremental=incremental))
    store = ResearchStore(db)

    dry = backfill(store, runs, dry_run=True)
    assert dry["counts"]["would_index"] == 2
    assert store.show_run("single")["candidate_id"] is None

    first = backfill(store, runs)
    second = backfill(store, runs)

    assert first["counts"]["indexed"] == 2
    assert second["counts"]["already_indexed"] == 2
    assert store.show_run("single")["incremental_status"] == "not_evaluated"
    indexed = store.show_run("incremental")
    assert indexed["delta_auc"] == 0.001
    assert indexed["classification"] == "INCONCLUSIVE"


def test_backfill_reports_missing_parent_and_invalid_summary(tmp_path):
    from alpha_lab.research_cli import backfill
    db = tmp_path / "experiments.db"
    runs = tmp_path / "runs"
    ExperimentRegistry(db)
    write_run(runs, "orphan", summary("orphan"))
    invalid = runs / "invalid"
    invalid.mkdir()
    add_parent(db, "invalid", invalid)
    (invalid / "summary.json").write_text("{not-json", encoding="utf-8")

    result = backfill(ResearchStore(db), runs)

    assert result["counts"]["missing_parent"] == 1
    assert result["counts"]["invalid"] == 1
    with ResearchStore(db).connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0


def test_backfill_prioritizes_missing_parent_for_unrelated_summary_shape(tmp_path):
    from alpha_lab.research_cli import backfill
    db = tmp_path / "experiments.db"
    runs = tmp_path / "runs"
    ExperimentRegistry(db)
    write_run(runs, "orphan-folder", {"status": "completed"})

    result = backfill(ResearchStore(db), runs)

    assert result["counts"]["missing_parent"] == 1
    assert result["counts"]["invalid"] == 0


def test_backfill_continues_after_malformed_metric(tmp_path):
    from alpha_lab.research_cli import backfill
    db = tmp_path / "experiments.db"
    runs = tmp_path / "runs"
    bad_dir = runs / "bad"
    good_dir = runs / "good"
    add_parent(db, "bad", bad_dir)
    add_parent(db, "good", good_dir)
    bad = summary("bad", incremental=True)
    bad["incremental_evaluation"]["baseline"]["auc"] = []
    write_run(runs, "bad", bad)
    write_run(runs, "good", summary("good"))

    result = backfill(ResearchStore(db), runs)

    assert result["counts"]["invalid"] == 1
    assert result["counts"]["indexed"] == 1


def test_backfill_evidence_never_creates_or_removes_decisions(tmp_path):
    from alpha_lab.research_cli import backfill
    db = tmp_path / "experiments.db"
    runs = tmp_path / "runs"
    run_id = "evidence"
    run_dir = runs / run_id
    add_parent(db, run_id, run_dir)
    payload = summary(run_id, incremental=True)
    payload.pop("research_feedback")
    payload["evidence_report"] = {
        "schema_version": 1,
        "run_id": run_id,
        "candidate": payload["candidate"],
        "evaluation_context": {"incremental_status": "evaluated"},
        "factor_evaluation": payload["factor_evaluation"],
        "model_evaluation": payload["model_evaluation"],
        "incremental_evaluation": payload["incremental_evaluation"],
    }
    write_run(runs, run_id, payload)
    store = ResearchStore(db)

    assert backfill(store, runs)["counts"]["indexed"] == 1
    assert store.list_decisions(run_id) == []
    store.create_decision(
        run_id=run_id,
        candidate_source_hash="a" * 64,
        supporting_evidence=[],
        opposing_evidence=[],
        conclusion="Keep researching.",
        next_hypothesis="Try another transform.",
        next_actions=["generate_candidate"],
    )
    before = store.list_decisions(run_id)

    assert backfill(store, runs)["counts"]["already_indexed"] == 1
    assert store.list_decisions(run_id) == before
