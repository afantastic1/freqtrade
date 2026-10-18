
import hashlib
import pytest

from alpha_lab import runner
from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.factors.registry import FactorSpec


class FakeConfig:
    """模拟一个包含内置因子的合法实验配置。"""

    factors = (FactorSpec("momentum", {"periods": 4}),)
    horizon = 4
    symbol = "BTC/USDT:USDT"
    timeframe = "1h"
    start = "2022-01-01"
    end = "2025-01-01"
    model = "logistic"
    train_months = 12
    retrain_months = 1
    test_start = "2023-01-01"
    test_end = "2025-01-01"

    @staticmethod
    def from_json(path):
        return FakeConfig()


def setup_runner(tmp_path, monkeypatch):
    db = tmp_path / "experiments.db"
    output_root = tmp_path / "runs"

    monkeypatch.setattr(runner, "DB_PATH", db)
    monkeypatch.setattr(runner, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(runner, "ExperimentConfig", FakeConfig)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"horizon":4}',
        encoding="utf-8",
    )

    return db, output_root, config_path


def test_runner_records_success(tmp_path, monkeypatch):
    db, output_root, config_path = setup_runner(
        tmp_path, monkeypatch
    )

    def fake_execute(
        config,
        run_id,
        config_hash,
        candidate_dir=None,
    ):
        result_dir = output_root / run_id
        result_dir.mkdir(parents=True)

        (result_dir / "summary.json").write_text(
            "{}",
            encoding="utf-8",
        )

        return {"run_id": run_id}

    monkeypatch.setattr(
        runner,
        "_execute_experiment",
        fake_execute,
    )

    result = runner.run_experiment(config_path)

    registry = ExperimentRegistry(db)
    record = registry.get_run(result["run_id"])

    assert record is not None
    assert record["status"] == "completed"
    assert record["result_dir"] == str(
        output_root / result["run_id"]
    )
    assert record["finished_at"] is not None


def test_runner_records_failure(tmp_path, monkeypatch):
    db, _, config_path = setup_runner(
        tmp_path, monkeypatch
    )

    def fake_execute(
        config,
        run_id,
        config_hash,
        candidate_dir=None,
    ):
        raise RuntimeError("Synthetic experiment failure")

    monkeypatch.setattr(
        runner,
        "_execute_experiment",
        fake_execute,
    )

    with pytest.raises(
        RuntimeError,
        match="Synthetic experiment failure",
    ):
        runner.run_experiment(config_path)

    config_hash = hashlib.sha256(
        b'{"horizon":4}'
    ).hexdigest()

    registry = ExperimentRegistry(db)
    records = registry.find_by_config(config_hash)

    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["finished_at"] is not None
    assert "Synthetic experiment failure" in records[0]["error"]


def test_runner_rejects_no_factors(tmp_path, monkeypatch):
    db = tmp_path / "experiments.db"

    monkeypatch.setattr(runner, "DB_PATH", db)

    class EmptyConfig:
        factors = ()

        @staticmethod
        def from_json(path):
            return EmptyConfig()

    monkeypatch.setattr(
        runner,
        "ExperimentConfig",
        EmptyConfig,
    )

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"factors":[]}',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="At least one factor",
    ):
        runner.run_experiment(config_path)

    # 无有效因子的请求不应建立实验记录
    assert not db.exists()


def test_index_failure_is_visible_and_completed_run_is_recoverable(
    tmp_path, monkeypatch
):
    db, output_root, config_path = setup_runner(tmp_path, monkeypatch)

    class Candidate:
        name = "test_factor"
        source_hash = "a" * 64
        parameters = {}
        prediction_horizon = 4

    candidate_dir = tmp_path / "test_factor"
    candidate_dir.mkdir()
    (candidate_dir / "candidate.json").write_text(
        '{"name":"test_factor","hypothesis":"test","parameters":{},'
        '"required_columns":["close"],"prediction_horizon":4}',
        encoding="utf-8",
    )
    (candidate_dir / "factor.py").write_text(
        "def compute(frame, **params):\n    return frame['close']\n",
        encoding="utf-8",
    )
    real_candidate = runner.submit_candidate(candidate_dir)
    monkeypatch.setattr(runner, "submit_candidate", lambda path: real_candidate)

    def fake_execute(config, run_id, config_hash, candidate_dir=None):
        summary = {
            "run_id": run_id,
            "config_hash": config_hash,
            "status": "completed",
            "candidate": {
                "name": "test_factor",
                "source_hash": real_candidate.source_hash,
                "parameters": {},
            },
            "factor_evaluation": {},
            "model_evaluation": {"model": {"auc": 0.5}},
            "incremental_evaluation": None,
        }
        (output_root / run_id / "summary.json").write_text(
            __import__("json").dumps(summary), encoding="utf-8"
        )
        return summary

    class FailingStore:
        def __init__(self, db_path):
            pass

        def index_summary(self, run_id, summary_path):
            raise RuntimeError("synthetic index failure")

    real_store = runner.ResearchStore
    monkeypatch.setattr(runner, "_execute_experiment", fake_execute)
    monkeypatch.setattr(runner, "ResearchStore", FailingStore, raising=False)

    with pytest.raises(RuntimeError, match="backfill"):
        runner.run_experiment(config_path, candidate_dir=candidate_dir)

    with ExperimentRegistry(db)._connect() as conn:
        row = conn.execute(
            "SELECT * FROM experiments ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "completed"
    assert (output_root / row["run_id"] / "summary.json").is_file()

    monkeypatch.setattr(runner, "ResearchStore", real_store)
    recovered = runner.run_experiment(config_path, candidate_dir=candidate_dir)
    assert recovered["reused"] is True
    assert real_store(db).show_run(row["run_id"])["candidate_id"] is not None
