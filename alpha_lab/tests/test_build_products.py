import pandas as pd
import pytest

from alpha_lab.data.build_products import EvaluatorLabelsV1, WorkerInputV2


def test_worker_asof_contains_only_available_rows():
    frame = pd.DataFrame({"decision_time": pd.to_datetime(["2025-01-01T00:00Z", "2025-01-01T00:05Z"], utc=True), "available_time": pd.to_datetime(["2025-01-01T00:01Z", "2025-01-01T00:06Z"], utc=True), "close": [1, 2]})
    got = WorkerInputV2(frame, ("available_time",)).asof(pd.Timestamp("2025-01-01T00:05Z"))
    assert got.close.tolist() == [1]


def test_worker_product_has_no_label_columns():
    with pytest.raises(ValueError, match="label"):
        WorkerInputV2(pd.DataFrame({"available_time": pd.to_datetime(["2025-01-01T00:00Z"], utc=True), "label_value": [1]}), ("available_time",))


def test_asof_rejects_naive_or_out_of_range_time():
    product = WorkerInputV2(pd.DataFrame({"available_time": pd.to_datetime(["2025-01-01T00:00Z"], utc=True)}), ("available_time",))
    with pytest.raises(ValueError):
        product.asof(pd.Timestamp("2025-01-01"))
    with pytest.raises(ValueError):
        product.asof(pd.Timestamp("2024-01-01T00:00Z"))


def test_evaluator_labels_are_index_aligned():
    index = pd.Index([1, 2])
    frame = pd.DataFrame({"label_value": [1., 2.], "decision_time": pd.date_range("2025-01-01", periods=2, tz="UTC")}, index=index)
    assert EvaluatorLabelsV1(frame, decision_index=index).frame.index.equals(index)
    with pytest.raises(ValueError, match="align"):
        EvaluatorLabelsV1(frame, decision_index=pd.Index([2, 3]))


def test_products_defensively_copy_frames():
    frame = pd.DataFrame({"available_time": pd.to_datetime(["2025-01-01T00:00Z"], utc=True), "close": [1]})
    product = WorkerInputV2(frame, ("available_time",))
    frame.loc[0, "close"] = 9
    assert product.frame.close.iloc[0] == 1
    exposed = product.frame
    exposed.loc[0, "close"] = 8
    assert product.frame.close.iloc[0] == 1
