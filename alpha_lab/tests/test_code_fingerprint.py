
from pathlib import Path

import pytest

from alpha_lab.cross_sectional_runner import _code_fingerprint


def test_same_filename_in_different_directories(tmp_path):
    label = tmp_path / "labels" / "cross_sectional.py"
    evaluator = tmp_path / "evaluation" / "cross_sectional.py"

    label.parent.mkdir()
    evaluator.parent.mkdir()

    label.write_text("label implementation")
    evaluator.write_text("evaluator implementation")

    combined = _code_fingerprint(
        [label, evaluator], root=tmp_path
    )
    only_evaluator = _code_fingerprint(
        [evaluator], root=tmp_path
    )

    assert combined != only_evaluator

    # 文件集合顺序不应影响哈希
    assert combined == _code_fingerprint(
        [evaluator, label], root=tmp_path
    )


def test_file_changes_affect_fingerprint(tmp_path):
    source = tmp_path / "factor.py"
    source.write_text("version 1")

    before = _code_fingerprint([source], root=tmp_path)

    source.write_text("version 2")

    after = _code_fingerprint([source], root=tmp_path)

    assert before != after


def test_duplicate_paths_rejected(tmp_path):
    source = tmp_path / "factor.py"
    source.write_text("some code")

    with pytest.raises(ValueError, match="duplicate code path"):
        _code_fingerprint(
            [source, source], root=tmp_path
        )
  