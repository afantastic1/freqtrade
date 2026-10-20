from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab.data.temporal_plan import build_temporal_plan, partition_temporal_samples


def request():
    raw = {
        "protocol_version": "2.0-research-request", "request_id": "temporal-test",
        "campaign_id": "example_campaign", "policy_version": "1",
        "campaign_policy_sha256": "0" * 64, "start": "2023-01-02T00:00:00Z",
        "end": "2023-01-02T00:30:00Z", "symbols": ["BTCUSDT"], "timeframe": "5m",
        "mode": "time_series", "data_types": ["futures_ohlcv"], "split": "discovery",
        "lookback_bars": 2, "label_horizon_bars": 2, "embargo_bars": 1,
    }
    # The example campaign's actual canonical digest is read from its trusted registry.
    import json
    from pathlib import Path
    policy_file = Path(__file__).parents[2] / "alpha_lab_contracts/campaigns/example_campaign-v1.json"
    policy = json.loads(policy_file.read_text())
    raw["campaign_policy_sha256"] = policy["policy_sha256"]
    return ResearchRequestV2.from_dict(raw, FileCampaignPolicyRegistry()), FileCampaignPolicyRegistry().get("example_campaign", "1", policy["policy_sha256"])


def test_temporal_plan_uses_half_open_study_window():
    req, policy = request()
    plan = build_temporal_plan(req, policy)
    assert plan.study_start == req.start and plan.study_end == req.end


def test_temporal_plan_reads_exact_lookback_slots():
    req, policy = request()
    plan = build_temporal_plan(req, policy)
    assert plan.warmup_start == req.start - timedelta(minutes=10)
    assert plan.warmup_end == req.start


def test_temporal_plan_never_crosses_selected_split():
    req, policy = request()
    req = replace(req, start=policy.splits["discovery"].start + timedelta(minutes=10), end=policy.splits["discovery"].start + timedelta(minutes=15))
    assert build_temporal_plan(req, policy).warmup_start == policy.splits["discovery"].start


def test_label_extension_includes_horizon_bar():
    req, policy = request()
    plan = build_temporal_plan(req, policy)
    assert plan.label_extension_end == req.end + timedelta(minutes=10)


def test_partition_purges_unavailable_train_labels_and_applies_embargo():
    labels = pd.DataFrame({"decision_time": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T00:05Z", "2023-01-01T00:10Z", "2023-01-01T00:20Z"], utc=True), "label_available_time": pd.to_datetime(["2023-01-01T00:05Z", "2023-01-01T00:20Z", "2023-01-01T00:16Z", "2023-01-01T00:25Z"], utc=True)})
    train, test = partition_temporal_samples(labels, train_end=pd.Timestamp("2023-01-01T00:15Z"), test_end=pd.Timestamp("2023-01-01T01:00Z"), timeframe="5m", embargo_bars=1)
    assert len(train) == 1
    assert test.decision_time.tolist() == [pd.Timestamp("2023-01-01T00:20Z")]


def test_reject_policy_fails_missing_required_slots():
    from alpha_lab.data.temporal_plan import enforce_required_slots
    expected = pd.date_range("2023-01-01", periods=3, freq="5min", tz="UTC")
    with pytest.raises(ValueError, match="missing"):
        enforce_required_slots(expected, expected[[0, 2]], "reject")


def test_preserve_keeps_sparse_events_without_fill():
    from alpha_lab.data.temporal_plan import preserve_sparse_events
    events = pd.DataFrame({"date": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T01:00Z"], utc=True), "value": [1, 2]})
    assert preserve_sparse_events(events).equals(events)
