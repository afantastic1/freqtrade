from __future__ import annotations

import hashlib

import pytest

from alpha_lab_service.request_registry_v2 import ResearchRequestRegistryV2
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry


def request_payload(**overrides):
    payload = {
        "protocol_version": "2.0-research-request",
        "request_id": "agent-request-001",
        "campaign_id": "dataset_builder_acceptance",
        "policy_version": "1",
        "campaign_policy_sha256": "b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f",
        "start": "2026-02-01T00:00:00Z",
        "end": "2026-02-02T00:00:00Z",
        "symbols": ["BTCUSDT"],
        "timeframe": "5m",
        "mode": "time_series",
        "data_types": ["futures_ohlcv"],
        "split": "holdout",
        "lookback_bars": 20,
        "label_horizon_bars": 3,
        "embargo_bars": 1,
    }
    payload.update(overrides)
    return payload


def registry(path):
    return ResearchRequestRegistryV2(path, FileCampaignPolicyRegistry())


def test_create_and_get_return_campaign_authorized_request(tmp_path):
    requests = registry(tmp_path / "trusted-requests.sqlite")

    created = requests.create(request_payload())

    assert created["request_id"] == "agent-request-001"
    assert created["campaign_id"] == "dataset_builder_acceptance"
    assert created["start"] == "2026-02-01T00:00:00.000000Z"
    assert created["symbols"] == ["BTCUSDT"]
    assert requests.get("agent-request-001") == created


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbols": ["DOGEUSDT"]},
        {"campaign_policy_sha256": "0" * 64},
        {"split": "discovery", "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        {"filesystem_path": "/etc/passwd"},
        {"dataset_manifest": {"dataset_id": "caller-controlled"}},
    ],
)
def test_create_rejects_unauthorized_scope_and_caller_owned_provenance(tmp_path, overrides):
    requests = registry(tmp_path / "trusted-requests.sqlite")

    with pytest.raises(ValueError):
        requests.create(request_payload(**overrides))


def test_create_is_idempotent_for_identical_request(tmp_path):
    requests = registry(tmp_path / "trusted-requests.sqlite")
    payload = request_payload()

    first = requests.create(payload)
    second = requests.create(payload)

    assert second == first
    assert requests.get("agent-request-001") == first


def test_create_rejects_conflicting_request_id_reuse(tmp_path):
    requests = registry(tmp_path / "trusted-requests.sqlite")
    requests.create(request_payload())

    with pytest.raises(ValueError, match="request ID already exists with different content"):
        requests.create(request_payload(
            start="2026-02-03T00:00:00Z", end="2026-02-04T00:00:00Z"
        ))


def test_get_is_read_only_and_unknown_request_is_explicit(tmp_path):
    database = tmp_path / "trusted-requests.sqlite"
    requests = registry(database)
    requests.create(request_payload())
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="unknown research request"):
        requests.get("missing-request")

    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert after == before
