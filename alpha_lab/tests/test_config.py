
import json

import pytest

from alpha_lab.config import ExperimentConfig


def valid_config():
    return {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": 4,
        "factors": [
            {
                "name": "momentum",
                "params": {"periods": 4},
            }
        ],
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }


def load_config(tmp_path, raw):
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return ExperimentConfig.from_json(path)


def test_valid_config(tmp_path):
    cfg = load_config(tmp_path, valid_config())

    assert cfg.horizon == 4
    assert len(cfg.factors) == 1
    assert cfg.factors[0].name == "momentum"


def test_protected_data_rejected(tmp_path):
    raw = valid_config()
    raw["end"] = "2026-01-01"
    raw["test_end"] = "2026-01-01"

    with pytest.raises(ValueError, match="allowed range"):
        load_config(tmp_path, raw)


def test_invalid_horizon_rejected(tmp_path):
    raw = valid_config()
    raw["horizon"] = 0

    with pytest.raises(ValueError, match="Invalid horizon"):
        load_config(tmp_path, raw)


def test_empty_factors_allowed_for_candidate(tmp_path):
    raw = valid_config()
    raw["factors"] = []

    config = load_config(tmp_path, raw)

    assert config.factors == ()


def test_unknown_config_key_rejected(tmp_path):
    raw = valid_config()
    raw["execute_shell"] = "echo hello"

    with pytest.raises(ValueError, match="Unknown config keys"):
        load_config(tmp_path, raw)


def test_invalid_test_window_rejected(tmp_path):
    raw = valid_config()
    raw["test_start"] = "2024-12-01"
    raw["test_end"] = "2024-01-01"

    with pytest.raises(ValueError, match="Invalid test window"):
        load_config(tmp_path, raw)


def test_research_code_identity_covers_factor_report_and_evidence():
    from alpha_lab import runner
    names = {path.name for path in runner.EVALUATION_CODE_PATHS}
    assert {"momentum.py", "factor_report.py", "evidence.py"} <= names
