import pandas as pd
import pytest

from alpha_lab_service.evaluator import evaluate_authorized_factor_output
from alpha_lab_service.tests.test_v2_chunks import make_run


def factor_series(run):
    index = pd.MultiIndex.from_tuples(
        [(stamp, symbol) for stamp in run.evaluation_timestamps for symbol in run.research_request.symbols],
        names=["open_time", "symbol"],
    )
    return pd.Series([1.0] * len(index), index=index, dtype=float)


def test_v2_evaluator_accepts_complete_authorized_projection():
    run = make_run(count=2)
    report = evaluate_authorized_factor_output(factor_series(run), run, identity="run-1")
    assert report["status"] in {"DIAGNOSTIC", "EXPLORATORY"}
    assert report["identity"] == "run-1"


def test_v2_evaluator_rejects_partial_or_reordered_projection():
    run = make_run(count=2)
    factor = factor_series(run)
    with pytest.raises(ValueError, match="complete authorized"):
        evaluate_authorized_factor_output(factor.iloc[:1], run, identity="run-1")
    with pytest.raises(ValueError, match="complete authorized"):
        evaluate_authorized_factor_output(factor.iloc[::-1], run, identity="run-1")
