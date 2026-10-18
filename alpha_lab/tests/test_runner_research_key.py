import json
from pathlib import Path

import pytest

from alpha_lab import runner
from alpha_lab.experiment_registry import ExperimentRegistry


def create_candidate(tmp_path, *, source="return frame['close']", horizon=4):
    folder = tmp_path / "reuse_factor"
    folder.mkdir(exist_ok=True)
    (folder / "candidate.json").write_text(json.dumps({
        "name": "reuse_factor",
        "hypothesis": "A deterministic candidate for reuse tests.",
        "parameters": {"window": 4},
        "required_columns": ["close"],
        "prediction_horizon": horizon,
    }), encoding="utf-8")
    (folder / "factor.py").write_text(
        f"def compute(frame, **params):\n    {source}\n",
        encoding="utf-8",
    )
    return folder


def create_config(tmp_path, *, periods=4, horizon=4):
    path = tmp_path / f"config-{periods}-{horizon}.json"
    path.write_text(json.dumps({
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": horizon,
        "factors": [{"name": "momentum", "params": {"periods": periods}}],
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }), encoding="utf-8")
    return path


def setup_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "DB_PATH", tmp_path / "experiments.db")
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path / "runs")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "BTC_USDT_USDT-1h-futures.feather").write_bytes(b"v1")
    monkeypatch.setattr(runner, "DATA_DIR", data_dir)
    calls = []

    def fake_execute(config, run_id, config_hash, candidate_dir=None):
        calls.append(run_id)
        candidate_raw = json.loads(
            (candidate_dir / "candidate.json").read_text(encoding="utf-8")
        )
        source_hash = __import__("hashlib").sha256(
            (candidate_dir / "factor.py").read_bytes()
        ).hexdigest()
        summary = {
            "run_id": run_id,
            "config_hash": config_hash,
            "status": "completed",
            "candidate": {
                **candidate_raw,
                "source_hash": source_hash,
                "source_path": str(candidate_dir / "factor.py"),
            },
            "factor_evaluation": {
                "reuse_factor_window_4": {
                    "overall": {"rank_ic": -0.01, "ic": -0.02},
                    "stability": {"valid_months": 2, "mean_monthly_rank_ic": -0.01},
                }
            },
            "model_evaluation": {"model": {"auc": 0.51}},
            "incremental_evaluation": None,
            "research_feedback": None,
        }
        (runner.OUTPUT_ROOT / run_id / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        return summary

    monkeypatch.setattr(runner, "_execute_experiment", fake_execute)
    return calls, data_dir


def test_research_key_reuse_force(tmp_path, monkeypatch):
    calls, _ = setup_runner(tmp_path, monkeypatch)
    candidate = create_candidate(tmp_path)
    config = create_config(tmp_path)

    first = runner.run_experiment(config, candidate_dir=candidate)
    reused = runner.run_experiment(config, candidate_dir=candidate)
    forced = runner.run_experiment(config, candidate_dir=candidate, force=True)

    assert reused["run_id"] == first["run_id"]
    assert reused["reused"] is True
    assert forced["run_id"] != first["run_id"]
    assert calls == [first["run_id"], forced["run_id"]]


def test_changed_source_baseline_or_data_does_not_reuse(tmp_path, monkeypatch):
    calls, data_dir = setup_runner(tmp_path, monkeypatch)
    candidate = create_candidate(tmp_path)
    config = create_config(tmp_path)
    first = runner.run_experiment(config, candidate_dir=candidate)

    (candidate / "factor.py").write_text(
        "def compute(frame, **params):\n    return -frame['close']\n",
        encoding="utf-8",
    )
    source_changed = runner.run_experiment(config, candidate_dir=candidate)
    baseline_changed = runner.run_experiment(
        create_config(tmp_path, periods=12), candidate_dir=candidate
    )
    (data_dir / "BTC_USDT_USDT-1h-futures.feather").write_bytes(b"v2-longer")
    data_changed = runner.run_experiment(config, candidate_dir=candidate)

    assert len({first["run_id"], source_changed["run_id"],
                baseline_changed["run_id"], data_changed["run_id"]}) == 4
    assert len(calls) == 4


def test_corrupt_completed_artifacts_are_not_reused(tmp_path, monkeypatch):
    calls, _ = setup_runner(tmp_path, monkeypatch)
    candidate = create_candidate(tmp_path)
    config = create_config(tmp_path)
    first = runner.run_experiment(config, candidate_dir=candidate)
    Path(first["report_path"]).unlink()

    second = runner.run_experiment(config, candidate_dir=candidate)

    assert second["run_id"] != first["run_id"]
    assert len(calls) == 2


def test_missing_candidate_manifest_is_not_reused(tmp_path, monkeypatch):
    calls, _ = setup_runner(tmp_path, monkeypatch)
    candidate = create_candidate(tmp_path)
    config = create_config(tmp_path)
    first = runner.run_experiment(config, candidate_dir=candidate)
    run_dir = runner.OUTPUT_ROOT / first["run_id"]
    (run_dir / "candidate_snapshot" / "reuse_factor" / "candidate.json").unlink()

    second = runner.run_experiment(config, candidate_dir=candidate)

    assert second["run_id"] != first["run_id"]
    assert len(calls) == 2


def test_running_research_key_is_claimed_once(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.db")
    first = registry.claim_run(config_hash="same", config_json="{}")

    with pytest.raises(RuntimeError, match=first):
        registry.claim_run(config_hash="same", config_json="{}")


def test_claim_detects_completed_run_created_after_reuse_check(tmp_path):
    from alpha_lab.experiment_registry import CompletedRunAvailable
    registry = ExperimentRegistry(tmp_path / "experiments.db")
    completed = registry.create_run(config_hash="same", config_json="{}")
    registry.complete_run(completed, tmp_path / "run")

    with pytest.raises(CompletedRunAvailable, match=completed):
        registry.claim_run(config_hash="same", config_json="{}")

    claimed = registry.claim_run(
        config_hash="same", config_json="{}",
        ignored_completed_run_ids=(completed,),
    )
    assert claimed != completed
