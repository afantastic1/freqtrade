import json
import pytest

from alpha_lab import candidate_cli
from alpha_lab import config as config_module
from alpha_lab.config import ExperimentConfig


def make_candidate(tmp_path):
    candidate_dir = tmp_path / "test_candidate"
    candidate_dir.mkdir()
    (candidate_dir / "candidate.json").write_text(
        json.dumps({
            "name": "test_candidate",
            "hypothesis": "A testable price-behaviour hypothesis.",
            "parameters": {"window": 12},
            "required_columns": ["close"],
            "prediction_horizon": 4,
        }),
        encoding="utf-8",
    )
    (candidate_dir / "factor.py").write_text(
        "def compute(frame, **params):\n    return frame['close']\n",
        encoding="utf-8",
    )
    return candidate_dir


def fake_result():
    return {
        "run_id": "run-1",
        "reused": False,
        "evaluation_mode": "incremental",
        "incremental_status": "evaluated",
        "factor_evaluation": {
            "test_candidate_window_12": {
                "overall": {"rank_ic": -0.12},
            }
        },
        "model_evaluation": {"model": {"auc": 0.53}},
    }


def test_default_candidate_baselines(tmp_path, monkeypatch, capsys):
    candidate_dir = make_candidate(tmp_path)
    calls = []

    def fake_run(config_path, *, candidate_dir, force=False):
        config = ExperimentConfig.from_json(config_path)
        calls.append((config, candidate_dir, force))
        return fake_result()

    monkeypatch.setattr(candidate_cli, "run_experiment", fake_run)

    candidate_cli.main(["run", str(candidate_dir)])

    assert len(calls) == 1
    config, submitted_dir, force = calls[0]
    assert [(item.name, item.params) for item in config.factors] == [
        ("momentum", {"periods": 4}),
        ("momentum", {"periods": 12}),
    ]
    assert submitted_dir == candidate_dir
    assert force is False
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_mode"] == "incremental"
    assert output["reused"] is False


def test_explicit_no_baseline(tmp_path, monkeypatch, capsys):
    candidate_dir = make_candidate(tmp_path)
    captured = []

    def fake_run(config_path, *, candidate_dir, force=False):
        captured.append(ExperimentConfig.from_json(config_path))
        result = fake_result()
        result.update({
            "evaluation_mode": "single_factor",
            "incremental_status": "not_evaluated",
            "incremental_evaluation": None,
            "research_feedback": None,
        })
        return result

    monkeypatch.setattr(candidate_cli, "run_experiment", fake_run)

    candidate_cli.main(["run", str(candidate_dir), "--no-baseline"])

    assert captured[0].factors == ()
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_mode"] == "single_factor"
    assert output["incremental_status"] == "not_evaluated"
    assert output["delta_auc"] is None


def test_baseline_config_preserves_explicit_factors(tmp_path, monkeypatch):
    candidate_dir = make_candidate(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": 4,
        "factors": [{
            "name": "momentum",
            "params": {"periods": 24},
            "alias": "explicit_mom24",
        }],
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }), encoding="utf-8")
    captured = []

    def fake_run(config_path, *, candidate_dir, force=False):
        captured.append((ExperimentConfig.from_json(config_path), force))
        return fake_result()

    monkeypatch.setattr(candidate_cli, "run_experiment", fake_run)
    candidate_cli.main([
        "run", str(candidate_dir),
        "--baseline-config", str(baseline_path),
        "--force",
    ])

    assert [(f.name, f.params, f.alias) for f in captured[0][0].factors] == [
        ("momentum", {"periods": 24}, "explicit_mom24"),
    ]
    assert captured[0][1] is True


def test_no_baseline_conflicts_with_baseline_config(tmp_path):
    candidate_dir = make_candidate(tmp_path)
    with pytest.raises(SystemExit):
        candidate_cli.main([
            "run", str(candidate_dir),
            "--no-baseline",
            "--baseline-config", str(tmp_path / "baseline.json"),
        ])


def test_default_baseline_factor_contract():
    factors = config_module.default_baseline_factors()
    assert [(factor.name, factor.params) for factor in factors] == [
        ("momentum", {"periods": 4}),
        ("momentum", {"periods": 12}),
    ]
