from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

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


@pytest.mark.parametrize("timestamp", ["2023-01-01", "2023-01-01T00:00:00", "2023-01-01T00:00:00+", "2023-02-01T01:00:00+00:60", "2023-02-01T01:00:00+00:99", "not-a-date"])
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


def test_request_schema_rejects_malformed_timestamp_with_format_checker() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas/research-request-v2.schema.json").read_text())
    raw = request_raw(start="2023-02-01T01:00:00+00:99")
    with pytest.raises(Exception):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2022-12-31T23:00:00Z", "2023-01-01T01:00:00Z"),
        ("2023-12-31T23:00:00Z", "2024-01-01T01:00:00Z"),
    ],
)
def test_request_rejects_interval_outside_selected_split(start: str, end: str) -> None:
    with pytest.raises(ValueError, match="outside the selected campaign split"):
        ResearchRequestV2.from_dict(request_raw(start=start, end=end), FileCampaignPolicyRegistry())


def test_request_rejects_fractional_precision_beyond_datetime_resolution() -> None:
    with pytest.raises(ValueError, match="precision"):
        ResearchRequestV2.from_dict(request_raw(start="2023-02-01T00:00:00.0000001Z"), FileCampaignPolicyRegistry())


def test_request_canonical_identity_normalizes_utc_but_preserves_symbol_order() -> None:
    registry = FileCampaignPolicyRegistry()
    first = ResearchRequestV2.from_dict(request_raw(start="2023-02-01T03:00:00+03:00", symbols=["BTCUSDT", "ETHUSDT"]), registry)
    equivalent = ResearchRequestV2.from_dict(request_raw(start="2023-02-01T00:00:00Z", symbols=["BTCUSDT", "ETHUSDT"]), registry)
    assert first.sha256() == equivalent.sha256()
    reversed_symbols = ResearchRequestV2.from_dict(request_raw(symbols=["ETHUSDT", "BTCUSDT"]), registry)
    assert first.sha256() != reversed_symbols.sha256()


def test_request_rejects_mode_allowed_by_registry_but_denied_by_campaign_policy() -> None:
    raw_policy = policy_raw()
    raw_policy["modes"] = ["time_series"]
    policy = CampaignPolicyV1.from_dict(with_policy_hash(raw_policy))
    request = request_raw(mode="cross_sectional", campaign_policy_sha256=policy.sha256())

    class Registry:
        def get(self, campaign_id: str, policy_version: str, policy_sha256: str) -> CampaignPolicyV1:
            return policy

    with pytest.raises(ValueError, match="mode is not authorized"):
        ResearchRequestV2.from_dict(request, Registry())


def with_policy_hash(raw: dict[str, object]) -> dict[str, object]:
    import hashlib

    unhashed = {key: value for key, value in raw.items() if key != "policy_sha256"}
    raw["policy_sha256"] = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return raw
