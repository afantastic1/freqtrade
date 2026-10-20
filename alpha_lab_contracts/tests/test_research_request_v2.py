from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyV1, FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2


def policy_raw() -> dict[str, object]:
    path = Path(__file__).parents[1] / "campaigns/example_campaign-v1.json"
    return json.loads(path.read_text())


def request_raw(**overrides: object) -> dict[str, object]:
    policy = CampaignPolicyV1.from_dict(policy_raw())
    result: dict[str, object] = {
        "protocol_version": "2.0-research-request",
        "request_id": "test-request-001",
        "campaign_id": policy.campaign_id,
        "policy_version": policy.policy_version,
        "campaign_policy_sha256": policy.sha256(),
        "start": "2023-02-01T00:00:00Z",
        "end": "2023-02-02T00:00:00Z",
        "symbols": ["BTCUSDT"],
        "timeframe": "5m",
        "mode": "time_series",
        "data_types": ["futures_ohlcv"],
        "split": "discovery",
        "lookback_bars": 20,
        "label_horizon_bars": 3,
        "embargo_bars": 1,
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize(
    ("split", "start", "end", "mode"),
    [
        ("discovery", "2023-02-01T00:00:00Z", "2023-02-02T00:00:00Z", "time_series"),
        ("validation", "2024-02-01T00:00:00Z", "2024-02-02T00:00:00Z", "cross_sectional"),
        ("holdout", "2025-06-01T00:00:00Z", "2025-06-02T00:00:00Z", "time_series"),
        ("holdout", "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z", "cross_sectional"),
    ],
)
def test_request_accepts_policy_authorized_dates_splits_and_modes(split: str, start: str, end: str, mode: str) -> None:
    request = ResearchRequestV2.from_dict(request_raw(split=split, start=start, end=end, mode=mode), FileCampaignPolicyRegistry())
    assert request.start.year == int(start[:4])
    assert request.split == split


def test_request_accepts_exclusive_split_end() -> None:
    request = ResearchRequestV2.from_dict(request_raw(start="2024-01-01T00:00:00Z", end="2025-01-01T00:00:00Z", split="validation"), FileCampaignPolicyRegistry())
    assert request.end.isoformat() == "2025-01-01T00:00:00+00:00"


@pytest.mark.parametrize("timestamp", ["2023-01-01", "2023-01-01T00:00:00", "2023-01-01T00:00:00+", "not-a-date"])
def test_request_rejects_naive_or_malformed_timestamps(timestamp: str) -> None:
    with pytest.raises(ValueError):
        ResearchRequestV2.from_dict(request_raw(start=timestamp), FileCampaignPolicyRegistry())


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2023-02-02T00:00:00Z", "end": "2023-02-01T00:00:00Z"},
        {"start": "2023-02-01T00:00:00Z", "end": "2023-02-01T00:00:00Z"},
        {"symbols": []},
        {"symbols": ["BTCUSDT", "BTCUSDT"]},
        {"data_types": []},
        {"data_types": ["futures_ohlcv", "futures_ohlcv"]},
        {"unknown": "nope"},
        {"mode": "unsupported"},
        {"split": "development"},
        {"lookback_bars": True},
        {"label_horizon_bars": False},
        {"embargo_bars": True},
        {"campaign_policy_sha256": "bad"},
        {"symbols": ["DOGEUSDT"]},
        {"timeframe": "1d"},
        {"data_types": ["open_interest"]},
        {"lookback_bars": 501},
        {"label_horizon_bars": 0},
        {"embargo_bars": 101},
        {"policy": {"symbols": ["*"], "modes": ["time_series"]}},
        {"registry_path": "/tmp/attacker.json"},
        {"permissions": {"symbols": ["*"]}},
    ],
)
def test_request_rejects_invalid_or_unauthorized_values(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        ResearchRequestV2.from_dict(request_raw(**changes), FileCampaignPolicyRegistry())


def test_request_schema_accepts_example_shape() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas/research-request-v2.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(request_raw())


def test_request_canonical_identity_normalizes_utc_but_preserves_symbol_order() -> None:
    registry = FileCampaignPolicyRegistry()
    first = ResearchRequestV2.from_dict(request_raw(start="2023-02-01T03:00:00+03:00"), registry)
    equivalent = ResearchRequestV2.from_dict(request_raw(start="2023-02-01T00:00:00Z"), registry)
    assert first.sha256() == equivalent.sha256()
    reversed_symbols = ResearchRequestV2.from_dict(request_raw(symbols=["ETHUSDT", "BTCUSDT"]), registry)
    assert first.sha256() != reversed_symbols.sha256()
