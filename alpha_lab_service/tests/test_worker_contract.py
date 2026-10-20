import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

_WORKER_PATH = Path(__file__).resolve().parents[2] / "containers/alpha-lab-factor-worker/worker.py"
_SPEC = importlib.util.spec_from_file_location("factor_worker_harness", _WORKER_PATH)
_WORKER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_WORKER)
normalize_output = _WORKER.normalize_output
validate_history = _WORKER.validate_history


AS_OF = datetime(2024, 1, 2, tzinfo=timezone.utc)


def _history():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2024-01-01T00:00:00Z"), "AAA"),
         (pd.Timestamp("2024-01-01T00:00:00Z"), "BBB")],
        names=["open_time", "symbol"],
    )
    frame = pd.DataFrame({"close": [10.0, 20.0]}, index=index)
    frame["available_time"] = pd.Timestamp("2024-01-01T00:01:00Z")
    return frame


def test_history_requires_sorted_unique_utc_rows_available_by_asof():
    frame = _history()
    assert validate_history(frame, as_of=AS_OF, symbols=["AAA", "BBB"]) is frame
    with pytest.raises(ValueError, match="sorted"):
        validate_history(frame.iloc[::-1], as_of=AS_OF, symbols=["AAA", "BBB"])
    future = frame.copy()
    future.iloc[0, future.columns.get_loc("available_time")] = pd.Timestamp("2024-01-03T00:00:00Z")
    with pytest.raises(ValueError, match="available"):
        validate_history(future, as_of=AS_OF, symbols=["AAA", "BBB"])
    future_open = frame.copy()
    future_open.index = pd.MultiIndex.from_arrays(
        [future_open.index.get_level_values("open_time") + pd.Timedelta(days=5),
         future_open.index.get_level_values("symbol")], names=["open_time", "symbol"]
    )
    with pytest.raises(ValueError, match="open_time"):
        validate_history(future_open, as_of=AS_OF, symbols=["AAA", "BBB"])


def test_worker_refuses_unknown_columns_that_could_carry_labels():
    frame = _history().assign(forward_return=0.5)
    with pytest.raises(ValueError, match="allowlisted"):
        validate_history(frame, as_of=AS_OF, symbols=["AAA", "BBB"])


def test_output_requires_exact_symbols_and_finite_scalar_values():
    result = normalize_output(pd.Series({"AAA": 1.0, "BBB": 2.0}), symbols=["AAA", "BBB"])
    assert result == {"AAA": 1.0, "BBB": 2.0}
    with pytest.raises(ValueError, match="symbols"):
        normalize_output(pd.Series({"AAA": 1.0}), symbols=["AAA", "BBB"])
    with pytest.raises(ValueError, match="finite"):
        normalize_output(pd.Series({"AAA": float("nan"), "BBB": 2.0}), symbols=["AAA", "BBB"])


def test_output_rejects_multidimensional_and_oversized_diagnostics():
    with pytest.raises(ValueError):
        normalize_output({"AAA": [1.0], "BBB": 2.0}, symbols=["AAA", "BBB"])
    with pytest.raises(ValueError, match="diagnostics"):
        normalize_output(pd.Series({"AAA": 1.0, "BBB": 2.0}), symbols=["AAA", "BBB"],
                         diagnostics={"note": "x" * 5000})
