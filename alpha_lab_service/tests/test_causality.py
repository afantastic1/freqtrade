import pandas as pd
import pytest

from alpha_lab_service.data_feed import build_asof_frame, validate_worker_steps


def _panel():
    rows = [
        ("2024-01-01T00:00:00Z", "AAA", "2024-01-01T01:00:00Z", 10.0, 999.0),
        ("2024-01-01T00:00:00Z", "BBB", "2024-01-01T01:00:00Z", 20.0, 999.0),
        ("2024-01-02T00:00:00Z", "AAA", "2024-01-02T01:00:00Z", 11.0, 999.0),
        ("2024-01-02T00:00:00Z", "BBB", "2024-01-02T02:00:00Z", 21.0, 999.0),
        ("2024-01-03T00:00:00Z", "AAA", "2024-01-03T01:00:00Z", 12.0, 999.0),
        ("2024-01-03T00:00:00Z", "BBB", "2024-01-03T01:00:00Z", 22.0, 999.0),
    ]
    return pd.DataFrame(rows, columns=["open_time", "symbol", "available_time", "close", "future_return"]).set_index(["open_time", "symbol"])


def test_asof_feed_uses_availability_cutoff_and_only_allowlisted_features():
    result = build_asof_frame(_panel(), as_of="2024-01-02T02:00:00Z", symbols=["AAA", "BBB"], required_columns=["close"])
    assert len(result) == 4
    assert "future_return" not in result.columns
    assert result.index.is_monotonic_increasing
    assert result["available_time"].max() == pd.Timestamp("2024-01-02T02:00:00Z")


def test_asof_feed_rejects_missing_shifted_duplicate_and_label_columns():
    frame = _panel()
    with pytest.raises(ValueError, match="symbols"):
        build_asof_frame(frame.drop(index=("2024-01-02T00:00:00Z", "BBB")), as_of="2024-01-03T00:00:00Z", symbols=["AAA", "BBB"], required_columns=["close"])
    with pytest.raises(ValueError, match="allowlisted"):
        build_asof_frame(frame, as_of="2024-01-03T00:00:00Z", symbols=["AAA", "BBB"], required_columns=["future_return"])
    duplicate = pd.concat([frame, frame.iloc[[0]]])
    with pytest.raises(ValueError, match="unique"):
        build_asof_frame(duplicate, as_of="2024-01-03T00:00:00Z", symbols=["AAA", "BBB"], required_columns=["close"])


def test_future_only_perturbation_cannot_change_asof_feed():
    original = build_asof_frame(_panel(), as_of="2024-01-02T00:30:00Z", symbols=["AAA", "BBB"], required_columns=["close"])
    changed = _panel()
    changed.loc[("2024-01-03T00:00:00Z", slice(None)), "close"] = -123456.0
    perturbed = build_asof_frame(changed, as_of="2024-01-02T00:30:00Z", symbols=["AAA", "BBB"], required_columns=["close"])
    pd.testing.assert_frame_equal(original, perturbed)


def test_feed_rejects_future_open_time_claimed_available_by_cutoff():
    frame = _panel()
    frame.loc[("2024-01-03T00:00:00Z", "AAA"), "available_time"] = "2024-01-02T00:00:00Z"
    with pytest.raises(ValueError, match="future open_time"):
        build_asof_frame(frame, as_of="2024-01-02T00:00:00Z", symbols=["AAA", "BBB"], required_columns=["close"])


def test_worker_step_validator_rejects_labels_future_rows_and_unbounded_history():
    valid = [{"type": "step", "as_of": "2024-01-02T00:00:00Z", "history": [
        {"open_time": "2024-01-01T00:00:00Z", "symbol": symbol,
         "available_time": "2024-01-01T01:00:00Z", "close": 1.0}
        for symbol in ("AAA", "BBB")
    ]}]
    assert validate_worker_steps(valid, symbols=["AAA", "BBB"], start="2024-01-01T00:00:00Z",
                                  end="2024-01-03T00:00:00Z", max_lookback=1) == valid
    labeled = [{**valid[0], "history": [{**row, "future_return": 1.0} for row in valid[0]["history"]]}]
    with pytest.raises(ValueError, match="allowlisted"):
        validate_worker_steps(labeled, symbols=["AAA", "BBB"], start="2024-01-01T00:00:00Z",
                              end="2024-01-03T00:00:00Z", max_lookback=1)
    shifted = [{**valid[0], "history": [{**row, "open_time": "2024-01-03T00:00:00Z"} for row in valid[0]["history"]]}]
    with pytest.raises(ValueError, match="cutoff"):
        validate_worker_steps(shifted, symbols=["AAA", "BBB"], start="2024-01-01T00:00:00Z",
                              end="2024-01-03T00:00:00Z", max_lookback=1)
    too_long = [{**valid[0], "history": valid[0]["history"] * 2}]
    with pytest.raises(ValueError, match="lookback"):
        validate_worker_steps(too_long, symbols=["AAA", "BBB"], start="2024-01-01T00:00:00Z",
                              end="2024-01-03T00:00:00Z", max_lookback=1)
    malformed = [{**valid[0], "history": [{**row, "close": "not-a-price"} for row in valid[0]["history"]]}]
    with pytest.raises(ValueError, match="numeric"):
        validate_worker_steps(malformed, symbols=["AAA", "BBB"], start="2024-01-01T00:00:00Z",
                              end="2024-01-03T00:00:00Z", max_lookback=1)
