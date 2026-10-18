
import json

import pytest

from alpha_lab.factors import candidate as candidate_module
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


def test_candidate_snapshot_immutable(tmp_path):
    folder, _ = create_candidate(tmp_path)
    original = submit_candidate(folder)
    run_dir = tmp_path / "runs" / "run-1"

    snapshot = candidate_module.snapshot_candidate(
        folder, run_dir, expected=original
    )
    archived_source = snapshot.spec.source_path.read_bytes()

    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n"
        "    return frame['volume']\n",
        encoding="utf-8",
    )

    assert snapshot.candidate_dir == (
        run_dir / "candidate_snapshot" / "volume_divergence"
    )
    assert snapshot.spec.source_path.read_bytes() == archived_source
    assert snapshot.spec.source_hash == original.source_hash
    assert snapshot.source_relative_path == (
        "candidate_snapshot/volume_divergence/factor.py"
    )


def test_snapshot_rejects_source_changed_after_validation(tmp_path):
    folder, _ = create_candidate(tmp_path)
    original = submit_candidate(folder)
    (folder / "factor.py").write_text(
        "def compute(frame, **params):\n"
        "    return frame['volume']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="changed before snapshot"):
        candidate_module.snapshot_candidate(
            folder, tmp_path / "run-2", expected=original
        )
