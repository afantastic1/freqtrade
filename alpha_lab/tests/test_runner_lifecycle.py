
import hashlib
import pytest

from alpha_lab import runner
from alpha_lab.experiment_registry import ExperimentRegistry


class FakeConfig:
    """模拟一个包含内置因子的合法实验配置。"""

    factors = ("mock_factor",)

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
