import json
from types import SimpleNamespace

import pandas as pd

from alpha_lab import runner
from alpha_lab.config import ExperimentConfig
from alpha_lab.factors.candidate import submit_candidate


def config(tmp_path, *, baseline):
    path = tmp_path / ("baseline.json" if baseline else "single.json")
    path.write_text(json.dumps({
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": 4,
        "factors": ([{
            "name": "momentum", "params": {"periods": 4}
        }] if baseline else []),
        "baseline_source": "candidate_cli_default" if baseline else "disabled",
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }), encoding="utf-8")
    return ExperimentConfig.from_json(path)


def candidate(tmp_path):
    folder = tmp_path / "mode_factor"
    folder.mkdir()
    (folder / "candidate.json").write_text(json.dumps({
        "name": "mode_factor",
        "hypothesis": "Mode test.",
        "parameters": {},
        "required_columns": ["close"],
        "prediction_horizon": 4,
    }), encoding="utf-8")
    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n    return frame['close']\n",
        encoding="utf-8",
    )
    return folder, submit_candidate(folder)


def model_result():
    predictions = pd.DataFrame({"probability": [0.5]})
    return SimpleNamespace(
        predictions=predictions,
        fold_metrics=pd.DataFrame({"auc": [0.5]}),
        metrics={"model": {"auc": 0.5, "logloss": 0.69, "brier": 0.25}},
    )


def setup_execution(tmp_path, monkeypatch, *, baseline):
    cfg = config(tmp_path, baseline=baseline)
    folder, spec = candidate(tmp_path)
    frame = pd.DataFrame({
        "open_time": pd.to_datetime(["2024-01-01"], utc=True),
        "close_time": pd.to_datetime(["2024-01-01 01:00"], utc=True),
    })
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        runner, "load_ohlcv", lambda **kwargs: SimpleNamespace(frame=frame)
    )
    monkeypatch.setattr(
        runner, "load_candidate", lambda path: (spec, lambda market, **params: None)
    )
    monkeypatch.setattr(
        runner.FactorValidator, "validate",
        lambda self, **kwargs: SimpleNamespace(
            passed=True, errors=[], valid_count=1, total_count=1
        ),
    )
    feature_names = [item.output_name for item in cfg.factors] + [spec.name]
    monkeypatch.setattr(
        runner, "build_features",
        lambda **kwargs: pd.DataFrame({name: [1.0] for name in feature_names}),
    )
    dataset = pd.DataFrame({
        "open_time": pd.to_datetime(["2024-01-01"], utc=True),
        "decision_time": pd.to_datetime(["2024-01-01 01:00"], utc=True),
        "label_available_time": pd.to_datetime(["2024-01-01 05:00"], utc=True),
        "reference_return": [0.01], "target": [1],
        **{name: [1.0] for name in feature_names},
    })
    monkeypatch.setattr(runner, "build_research_dataset", lambda **kwargs: dataset)
    monkeypatch.setattr(runner, "evaluate_factor", lambda **kwargs: {
        "quality": {},
        "overall": {"ic": -0.1, "rank_ic": -0.2},
        "stability": {"valid_months": 1, "mean_monthly_rank_ic": -0.2},
        "monthly": pd.DataFrame(), "yearly": pd.DataFrame(),
        "deciles": pd.DataFrame(),
    })
    return cfg, folder


def test_three_model_no_duplicate_train(tmp_path, monkeypatch):
    cfg, folder = setup_execution(tmp_path, monkeypatch, baseline=True)
    calls = {"incremental": 0, "ordinary": 0}
    variants = {name: model_result() for name in ("baseline", "candidate", "combined")}
    incremental = {
        "factor_similarity": {},
        "baseline": {"auc": 0.5}, "candidate": {"auc": 0.5},
        "combined": {"auc": 0.5},
        "incremental": {"auc": 0.0, "logloss": 0.0, "brier": 0.0},
        "stability": {"monthly": [], "robust_positive": False},
        "sample_alignment": {"prediction_rows": 1},
    }

    def fake_incremental(**kwargs):
        calls["incremental"] += 1
        return incremental, variants

    def fake_model(**kwargs):
        calls["ordinary"] += 1
        return model_result()

    monkeypatch.setattr(runner, "evaluate_incremental", fake_incremental)
    monkeypatch.setattr(runner, "evaluate_model", fake_model)

    result = runner._execute_experiment(cfg, "run-mode", "key", folder)

    assert calls == {"incremental": 1, "ordinary": 0}
    assert result["evaluation_mode"] == "incremental"
    assert result["incremental_status"] == "evaluated"
    assert result["baseline"]["source"] == "candidate_cli_default"
    assert result["baseline"]["factors"][0]["feature_column"] == "momentum_periods_4"
    assert result["factor_evaluation"]["mode_factor"]["overall"]["rank_ic"] == -0.2
    assert "research_feedback" not in result
    evidence = result["evidence_report"]
    assert evidence["run_id"] == "run-mode"
    assert evidence["candidate"]["source_hash"]
    assert evidence["incremental_evaluation"] == incremental
    assert evidence["evaluation_context"]["baseline"] == result["baseline"]
    assert evidence["evaluation_context"]["bootstrap"] == {
        "samples": runner.BOOTSTRAP_SAMPLES,
        "block_sizes": list(runner.BOOTSTRAP_BLOCK_SIZES),
        "status": "evaluated",
    }
    assert evidence["evaluation_context"]["data"]["label_horizon"] == 4
    assert evidence["evaluation_context"]["model"]["validation_protocol"] == (
        "walk_forward"
    )


def test_explicit_single_factor_metadata(tmp_path, monkeypatch):
    cfg, folder = setup_execution(tmp_path, monkeypatch, baseline=False)
    monkeypatch.setattr(runner, "evaluate_model", lambda **kwargs: model_result())

    result = runner._execute_experiment(cfg, "run-single", "key", folder)

    assert result["evaluation_mode"] == "single_factor"
    assert result["incremental_status"] == "not_evaluated"
    assert result["incremental_evaluation"] is None
    assert "research_feedback" not in result
    assert result["baseline"] is None
    evidence = result["evidence_report"]
    assert evidence["run_id"] == "run-single"
    assert evidence["incremental_evaluation"] is None
    assert evidence["evaluation_context"]["incremental_status"] == (
        "not_evaluated"
    )
