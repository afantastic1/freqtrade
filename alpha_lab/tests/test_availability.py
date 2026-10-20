from datetime import timedelta

import pandas as pd
import pytest

from data_engineering.sources.contracts import DatasetIdentity
from alpha_lab.data.availability import (
    AvailabilityEvidenceV1, AvailabilityRuleV1, AvailabilityRuleRegistry,
    asof_join, assign_availability,
)


IDENTITY = DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "5m")


def test_fixed_delay_ohlcv_uses_close_time_plus_registered_delay():
    rule = AvailabilityRuleV1("ohlcv-close", IDENTITY, mode="fixed_delay", event_time_field="date", fixed_delay=timedelta(seconds=30))
    frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True), "close_time": pd.to_datetime(["2025-01-01T00:05:00Z"], utc=True), "close": [1.]})
    got = assign_availability(frame, IDENTITY, "a" * 64, rule, None)
    assert got.available_time.iloc[0] == pd.Timestamp("2025-01-01T00:05:30Z")
    assert got.pit_status.iloc[0] == "ASSUMED"
    assert got.availability_rule_sha256.iloc[0] == rule.rule_sha256


def test_evidence_must_match_pinned_dataset_and_every_row():
    rule = AvailabilityRuleV1("source", IDENTITY, mode="source_evidence", event_time_field="date")
    frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"], utc=True)})
    evidence = AvailabilityEvidenceV1(IDENTITY.dataset_id(), "b" * 64, pd.DataFrame({"row_key": [0], "available_time": pd.to_datetime(["2025-01-01T00:06:00Z"], utc=True)}))
    with pytest.raises(ValueError, match="manifest|row"):
        assign_availability(frame, IDENTITY, "a" * 64, rule, evidence)


def test_asof_excludes_late_event_availability():
    decisions = pd.DataFrame({"decision_time": pd.to_datetime(["2025-01-01T00:05:00Z"], utc=True)})
    events = pd.DataFrame({"event_time": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True), "available_time": pd.to_datetime(["2025-01-01T00:06:00Z"], utc=True), "value": [2]})
    assert pd.isna(asof_join(decisions, events, decision_time="decision_time").value.iloc[0])


def test_asof_enforces_event_max_age():
    decisions = pd.DataFrame({"decision_time": pd.to_datetime(["2025-01-01T02:00:00Z"], utc=True)})
    events = pd.DataFrame({"event_time": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True), "available_time": pd.to_datetime(["2025-01-01T00:01:00Z"], utc=True), "value": [2]})
    assert pd.isna(asof_join(decisions, events, decision_time="decision_time", max_age=pd.Timedelta("1h")).value.iloc[0])


def test_unverified_event_is_rejected_from_worker_view():
    rule = AvailabilityRuleV1("source", IDENTITY, mode="source_evidence", event_time_field="date")
    frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True)})
    got = assign_availability(frame, IDENTITY, "a" * 64, rule, None)
    assert got.pit_status.iloc[0] == "UNVERIFIED"
    with pytest.raises(ValueError, match="UNVERIFIED"):
        asof_join(pd.DataFrame({"decision_time": pd.to_datetime(["2025-01-01T00:01:00Z"], utc=True)}), got, decision_time="decision_time")


def test_evidence_rows_reject_duplicate_or_naive_times():
    with pytest.raises(ValueError):
        AvailabilityEvidenceV1("dataset", "a" * 64, pd.DataFrame({"row_key": [1, 1], "available_time": pd.to_datetime(["2025-01-01T00:00:00Z"] * 2, utc=True)}))
    with pytest.raises(ValueError):
        AvailabilityEvidenceV1("dataset", "a" * 64, pd.DataFrame({"row_key": [1], "available_time": [pd.Timestamp("2025-01-01")]}))
