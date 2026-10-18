import hashlib
import json

from alpha_lab import runner


def create_candidate(tmp_path):
    folder = tmp_path / "snapshot_factor"
    folder.mkdir()
    (folder / "candidate.json").write_text(json.dumps({
        "name": "snapshot_factor",
        "hypothesis": "The snapshot is the executable research input.",
        "parameters": {"window": 4},
        "required_columns": ["close"],
        "prediction_horizon": 4,
    }), encoding="utf-8")
    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n    return frame['close']\n",
        encoding="utf-8",
    )
    return folder


def create_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": 4,
        "factors": [],
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }), encoding="utf-8")
    return path


def test_snapshot_hash_matches_loaded_code(tmp_path, monkeypatch):
    candidate_dir = create_candidate(tmp_path)
    config_path = create_config(tmp_path)
    monkeypatch.setattr(runner, "DB_PATH", tmp_path / "experiments.db")
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path / "runs")
    observed = {}

    def fake_execute(config, run_id, config_hash, candidate_dir=None):
        source = candidate_dir / "factor.py"
        observed["candidate_dir"] = candidate_dir
        observed["source_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()
        summary = {
            "run_id": run_id,
            "status": "completed",
            "candidate": {
                "name": "snapshot_factor",
                "source_hash": observed["source_hash"],
            },
        }
        (runner.OUTPUT_ROOT / run_id / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        return summary

    monkeypatch.setattr(runner, "_execute_experiment", fake_execute)

    result = runner.run_experiment(config_path, candidate_dir=candidate_dir)

    run_dir = runner.OUTPUT_ROOT / result["run_id"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert observed["candidate_dir"] == (
        run_dir / "candidate_snapshot" / "snapshot_factor"
    )
    assert observed["source_hash"] == manifest["candidate"]["source_hash"]
    assert manifest["candidate"]["source_path"] == (
        "candidate_snapshot/snapshot_factor/factor.py"
    )
    assert manifest["candidate"]["manifest_path"] == (
        "candidate_snapshot/snapshot_factor/candidate.json"
    )
