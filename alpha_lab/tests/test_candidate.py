
import json

import pytest

from alpha_lab.factors.candidate import submit_candidate


def create_candidate(tmp_path):
    folder = tmp_path / "volume_divergence"
    folder.mkdir()

    manifest = {
        "name": "volume_divergence",
        "hypothesis": "Test hypothesis",
        "parameters": {"momentum_periods": 4},
        "required_columns": ["close", "volume"],
        "prediction_horizon": 4,
    }

    (folder / "candidate.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n"
        "    return frame['close']\n",
        encoding="utf-8",
    )

    return folder, manifest


def test_valid_candidate(tmp_path):
    folder, _ = create_candidate(tmp_path)

    result = submit_candidate(folder)

    assert result.name == "volume_divergence"
    assert len(result.source_hash) == 64


def test_invalid_name(tmp_path):
    folder, manifest = create_candidate(tmp_path)
    manifest["name"] = "../escape"

    (folder / "candidate.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name"):
        submit_candidate(folder)


def test_unknown_column(tmp_path):
    folder, manifest = create_candidate(tmp_path)
    manifest["required_columns"] = ["future_return"]

    (folder / "candidate.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="columns"):
        submit_candidate(folder)


def test_missing_source(tmp_path):
    folder, _ = create_candidate(tmp_path)
    (folder / "factor.py").unlink()

    with pytest.raises(ValueError, match="Missing"):
        submit_candidate(folder)


def test_source_change_updates_hash(tmp_path):
    folder, _ = create_candidate(tmp_path)

    first = submit_candidate(folder)

    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n"
        "    return frame['volume']\n",
        encoding="utf-8",
    )

    second = submit_candidate(folder)

    assert first.source_hash != second.source_hash
