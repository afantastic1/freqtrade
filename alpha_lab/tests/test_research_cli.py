import json

import pytest

from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore


def seed(db, tmp_path):
    registry = ExperimentRegistry(db)
    failed = registry.create_run(config_hash="failed", config_json="{}")
    registry.fail_run(failed, "ValueError: rejected candidate")

    run_dir = tmp_path / "runs" / "completed"
    run_dir.mkdir(parents=True)
    completed = registry.create_run(config_hash="completed", config_json="{}")
    registry.complete_run(completed, run_dir)
    payload = {
        "run_id": completed,
        "status": "completed",
        "candidate": {
            "name": "query_factor", "source_hash": "a" * 64,
            "parameters": {}, "hypothesis": "Query test.",
        },
        "factor_evaluation": {
            "query_factor": {
                "overall": {"rank_ic": -0.1, "ic": -0.05},
                "stability": {"valid_months": 2, "mean_monthly_rank_ic": -0.08},
            }
        },
        "model_evaluation": {"model": {"auc": 0.52}},
        "incremental_evaluation": None,
        "research_feedback": None,
    }
    report = run_dir / "summary.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    ResearchStore(db).index_summary(completed, report)
    return failed, completed


def test_research_cli_list_and_show_json(tmp_path, capsys):
    from alpha_lab import research_cli
    db = tmp_path / "experiments.db"
    failed, completed = seed(db, tmp_path)

    research_cli.main([
        "list", "--candidate", "query_factor", "--limit", "5", "--json"
    ], db_path=db, runs_root=tmp_path / "runs")
    listed = json.loads(capsys.readouterr().out)
    assert [row["run_id"] for row in listed] == [completed]
    assert "monthly" not in json.dumps(listed)
    assert listed[0]["report_path"].endswith("summary.json")

    research_cli.main([
        "show", "--run-id", completed, "--json"
    ], db_path=db, runs_root=tmp_path / "runs")
    shown = json.loads(capsys.readouterr().out)
    assert shown["candidate_name"] == "query_factor"
    assert shown["rank_ic"] == -0.1

    research_cli.main([
        "list", "--status", "failed", "--json"
    ], db_path=db, runs_root=tmp_path / "runs")
    failures = json.loads(capsys.readouterr().out)
    assert [row["run_id"] for row in failures] == [failed]
    assert "rejected candidate" in failures[0]["error"]


def test_research_cli_show_unknown_exits(tmp_path):
    from alpha_lab import research_cli
    db = tmp_path / "experiments.db"
    ExperimentRegistry(db)

    with pytest.raises(SystemExit):
        research_cli.main(
            ["show", "--run-id", "missing", "--json"],
            db_path=db,
            runs_root=tmp_path / "runs",
        )


def decision_file(tmp_path, **updates):
    payload = {
        "supporting_evidence": [{"metric": "delta_auc", "value": 0.001}],
        "opposing_evidence": [{"metric": "robust_positive", "value": False}],
        "conclusion": "Continue cautiously.",
        "next_hypothesis": "Reduce baseline redundancy.",
        "next_actions": ["generate_variant", "run_experiment"],
        "agent_identity": "cli-test-agent",
    }
    payload.update(updates)
    path = tmp_path / f"decision-{len(list(tmp_path.glob('decision-*')))}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_research_cli_appends_and_lists_decisions_json(tmp_path, capsys):
    from alpha_lab import research_cli
    db = tmp_path / "experiments.db"
    _, run_id = seed(db, tmp_path)

    for conclusion in ("First conclusion", "Revised conclusion"):
        research_cli.main([
            "decide", "--run-id", run_id, "--source-hash", "a" * 64,
            "--decision-file", str(decision_file(tmp_path, conclusion=conclusion)),
            "--json",
        ], db_path=db, runs_root=tmp_path / "runs")
        created = json.loads(capsys.readouterr().out)
        assert created["run_id"] == run_id
        assert created["conclusion"] == conclusion

    research_cli.main([
        "decisions", "--run-id", run_id, "--json"
    ], db_path=db, runs_root=tmp_path / "runs")
    decisions = json.loads(capsys.readouterr().out)
    assert [item["conclusion"] for item in decisions] == [
        "First conclusion", "Revised conclusion"
    ]


def test_research_cli_rejects_missing_fields_and_wrong_source(tmp_path):
    from alpha_lab import research_cli
    db = tmp_path / "experiments.db"
    _, run_id = seed(db, tmp_path)
    missing = decision_file(tmp_path)
    payload = json.loads(missing.read_text())
    payload.pop("next_actions")
    missing.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit):
        research_cli.main([
            "decide", "--run-id", run_id, "--source-hash", "a" * 64,
            "--decision-file", str(missing), "--json",
        ], db_path=db, runs_root=tmp_path / "runs")

    with pytest.raises(ValueError, match="source_hash"):
        research_cli.main([
            "decide", "--run-id", run_id, "--source-hash", "b" * 64,
            "--decision-file", str(decision_file(tmp_path)), "--json",
        ], db_path=db, runs_root=tmp_path / "runs")
