import pandas as pd
import pytest

from alpha_lab.data.build_products import (
    DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2,
)
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.authorization_v2 import (
    MappingResearchRequestRegistryV1, resolve_authorized_run,
)


def request_payload():
    return {
        "research_request_id": "example-request-001",
        "source": "def factor(frame, params): return 1.0",
        "params": {},
        "worker_input_schema_id": "ohlcv-v1",
        "worker_input_schema_version": "1",
        "execution_mode": "stateless",
    }


def research_request():
    return {
        "protocol_version": "2.0-research-request",
        "request_id": "example-request-001",
        "campaign_id": "example_campaign",
        "policy_version": "1",
        "campaign_policy_sha256": "041efb79ddf9b88a71ff071cbfb52f833420658ff888cf6b6d87bb5f0a7ef209",
        "start": "2025-06-01T00:00:00Z",
        "end": "2025-06-02T00:00:00Z",
        "symbols": ["BTCUSDT"],
        "timeframe": "5m",
        "mode": "time_series",
        "data_types": ["futures_ohlcv"],
        "split": "holdout",
        "lookback_bars": 20,
        "label_horizon_bars": 3,
        "embargo_bars": 1,
    }


class FakeDatasetBuilder:
    def __init__(self, *, manifest_marker="one", label_times=None):
        self.received = None
        self.manifest_marker = manifest_marker
        self.label_times = label_times

    def build(self, request, *, options, universe=None):
        self.received = (request, options, universe)
        timestamps = pd.to_datetime(["2025-06-01T00:05:00Z", "2025-06-01T00:10:00Z"], utc=True)
        worker = WorkerInputV2(pd.DataFrame({
            "open_time": timestamps - pd.Timedelta(minutes=5),
            "close_time": timestamps,
            "available_time": timestamps,
            "decision_time": timestamps,
            "open": [100.0, 101.0], "high": [102.0, 103.0], "low": [99.0, 100.0],
            "close": [101.0, 102.0], "volume": [10.0, 12.0], "symbol": "BTCUSDT",
        }), ("available_time",))
        labels = EvaluatorLabelsV1(pd.DataFrame({
            "decision_time": self.label_times if self.label_times is not None else timestamps,
            "label_value": [0.01, -0.02],
        }))
        return DatasetBuildResultV2(
            worker, labels, DatasetBuildReportV1(), {"marker": self.manifest_marker}
        )


def resolve(*, builder=None, request_id="example-request-001", raw_request=None,
            worker_identity="sha256:" + "a" * 64, evaluator_identity="evaluator-v1",
            factor_overrides=None):
    trusted_options = DatasetBuildOptionsV1("preserve", "futures_ohlcv", {}, {}, {})
    records = {} if request_id != "example-request-001" else {request_id: raw_request or research_request()}
    registry = MappingResearchRequestRegistryV1(records)
    return resolve_authorized_run(
        request_payload() | {"research_request_id": request_id} | (factor_overrides or {}),
        research_request_registry=registry,
        policy_registry=FileCampaignPolicyRegistry(),
        dataset_builder=builder or FakeDatasetBuilder(),
        dataset_options=trusted_options,
        worker_identity=worker_identity,
        evaluator_identity=evaluator_identity,
    )


def test_authorized_request_is_resolved_and_only_trusted_request_reaches_builder():
    builder = FakeDatasetBuilder()
    authorized = resolve(builder=builder)
    request, options, universe = builder.received
    assert request.request_id == "example-request-001"
    assert request.symbols == ("BTCUSDT",)
    assert request.split == "holdout"
    assert options.missing_data_policy == "preserve"
    assert universe is None
    assert authorized.research_request == request
    assert len(authorized.evaluation_timestamps) == 2
    assert authorized.evaluation_timestamps[0] == pd.Timestamp("2025-06-01T00:05:00Z")


@pytest.mark.parametrize("override", [
    {"symbols": ["ETHUSDT"]}, {"start": "2026-01-01T00:00:00Z"},
    {"split": "discovery"}, {"source_adapter": "attacker"},
    {"data_types": ["funding_rate"]}, {"label_value": 0.2},
    {"path": "/tmp/data"}, {"resource_profile": "large"},
    {"authorization": {"admin": True}},
])
def test_factor_request_rejects_caller_supplied_authority_fields(override):
    with pytest.raises(ValueError):
        resolve(factor_overrides=override)


def test_unknown_research_request_id_is_rejected_before_build():
    builder = FakeDatasetBuilder()
    with pytest.raises(ValueError, match="unknown research request"):
        resolve(builder=builder, request_id="not-registered")
    assert builder.received is None


def test_unregistered_state_schema_is_rejected_before_dataset_build():
    builder = FakeDatasetBuilder()
    with pytest.raises(ValueError, match="state schema"):
        resolve(builder=builder, factor_overrides={
            "execution_mode": "stateful", "state_schema_id": "attacker-schema", "state_schema_version": "1",
        })
    assert builder.received is None


def test_identity_changes_with_worker_evaluator_and_dataset_identity():
    baseline = resolve().identity_hash
    other_worker = resolve(worker_identity="sha256:" + "b" * 64).identity_hash
    other_evaluator = resolve(evaluator_identity="evaluator-v2").identity_hash
    other_dataset = resolve(builder=FakeDatasetBuilder(manifest_marker="two")).identity_hash
    assert len({baseline, other_worker, other_evaluator, other_dataset}) == 4


def test_evaluator_labels_are_not_part_of_worker_input_or_canonical_request():
    authorized = resolve()
    assert not any("label" in column.lower() for column in authorized.dataset.worker_input.frame.columns)
    assert "label_value" not in authorized.canonical_request_record


def test_evaluation_schedule_comes_from_worker_product_not_evaluator_labels():
    different_labels = pd.to_datetime(["2025-06-01T00:15:00Z", "2025-06-01T00:20:00Z"], utc=True)
    authorized = resolve(builder=FakeDatasetBuilder(label_times=different_labels))
    assert authorized.evaluation_timestamps == (
        pd.Timestamp("2025-06-01T00:05:00Z"), pd.Timestamp("2025-06-01T00:10:00Z")
    )

