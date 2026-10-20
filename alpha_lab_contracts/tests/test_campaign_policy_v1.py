from __future__ import annotations

import json
import hashlib

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

import alpha_lab_contracts.campaign_policy_v1 as policy_module
from alpha_lab_contracts.campaign_policy_v1 import (
    CampaignPolicyV1,
    FileCampaignPolicyRegistry,
)


def policy_dict() -> dict[str, object]:
    return {
        "protocol_version": "1.0-campaign-policy",
        "campaign_id": "example_campaign",
        "policy_version": "1",
        "policy_sha256": "0" * 64,
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframes": ["5m", "1h"],
        "modes": ["time_series", "cross_sectional"],
        "data_types": ["futures_ohlcv", "funding_rate"],
        "splits": {
            "discovery": {"start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"},
            "validation": {"start": "2024-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
            "holdout": {"start": "2025-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"},
        },
        "parameter_bounds": {
            "lookback_bars": {"min": 1, "max": 500},
            "label_horizon_bars": {"min": 1, "max": 100},
            "embargo_bars": {"min": 0, "max": 100},
        },
    }


def with_hash(raw: dict[str, object]) -> dict[str, object]:
    unhashed = {key: value for key, value in raw.items() if key != "policy_sha256"}
    raw["policy_sha256"] = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return raw


def test_policy_canonical_hash_is_stable_and_ignores_hash_field() -> None:
    first = CampaignPolicyV1.from_dict(with_hash(policy_dict()))
    second_raw = dict(policy_dict())
    second_raw["policy_sha256"] = first.sha256()
    second = CampaignPolicyV1.from_dict(second_raw)

    assert first.sha256() == second.sha256()
    assert first.canonical_json() == second.canonical_json()


def test_policy_hash_rejects_fractional_window_change_with_original_hash() -> None:
    raw = with_hash(policy_dict())
    raw["splits"]["discovery"]["start"] = "2023-01-01T00:00:00.500000Z"  # type: ignore[index]
    with pytest.raises(ValueError, match="policy_sha256"):
        CampaignPolicyV1.from_dict(raw)


def test_policy_nested_authorization_mappings_are_immutable() -> None:
    policy = CampaignPolicyV1.from_dict(with_hash(policy_dict()))
    with pytest.raises(TypeError):
        policy.splits["discovery"] = policy.splits["holdout"]  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.parameter_bounds["lookback_bars"] = (1, 2)  # type: ignore[index]


def test_registry_keeps_old_policy_identity_when_new_version_is_added(tmp_path, monkeypatch) -> None:
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    old_raw = json.loads((__import__("pathlib").Path(__file__).parents[1] / "campaigns/example_campaign-v1.json").read_text())
    (campaigns / "example_campaign-v1.json").write_text(json.dumps(old_raw))
    new_raw = dict(old_raw)
    new_raw["policy_version"] = "2"
    new_raw["symbols"] = [*old_raw["symbols"], "SOLUSDT"]  # type: ignore[misc]
    with_hash(new_raw)
    (campaigns / "example_campaign-v2.json").write_text(json.dumps(new_raw))
    monkeypatch.setattr(policy_module, "CAMPAIGNS_ROOT", campaigns)
    registry = FileCampaignPolicyRegistry()

    old_policy = registry.get("example_campaign", "1", str(old_raw["policy_sha256"]))
    new_policy = registry.get("example_campaign", "2", str(new_raw["policy_sha256"]))
    assert old_policy.policy_version == "1"
    assert old_policy.symbols == ("BTCUSDT", "ETHUSDT")
    assert new_policy.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def test_policy_schema_accepts_policy_fixture() -> None:
    schema = json.loads((__import__("pathlib").Path(__file__).parents[1] / "schemas/campaign-policy-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(with_hash(policy_dict()))


def test_policy_schema_rejects_unknown_utc_offset() -> None:
    schema = json.loads((__import__("pathlib").Path(__file__).parents[1] / "schemas/campaign-policy-v1.schema.json").read_text())
    raw = with_hash(policy_dict())
    raw["splits"]["discovery"]["start"] = "2023-01-01T00:00:00-00:00"  # type: ignore[index]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(raw)


@pytest.mark.parametrize("mutation", ["overlap", "reversed", "missing_split", "unknown_key", "bad_hash", "malformed_timestamp"])
def test_policy_rejects_invalid_records(mutation: str) -> None:
    raw = with_hash(policy_dict())
    if mutation == "overlap":
        raw["splits"]["validation"]["start"] = "2023-12-31T00:00:00Z"  # type: ignore[index]
    elif mutation == "reversed":
        raw["splits"]["discovery"]["end"] = "2022-01-01T00:00:00Z"  # type: ignore[index]
    elif mutation == "missing_split":
        del raw["splits"]["holdout"]  # type: ignore[index]
    elif mutation == "unknown_key":
        raw["admin_approved"] = True
    elif mutation == "malformed_timestamp":
        raw["splits"]["discovery"]["start"] = "2023-02-01T00:00:00+00:99"  # type: ignore[index]
    else:
        raw["policy_sha256"] = "f" * 64

    with pytest.raises((ValueError, KeyError)):
        CampaignPolicyV1.from_dict(raw)


def test_registry_rejects_unknown_and_traversal_ids() -> None:
    registry = FileCampaignPolicyRegistry()
    with pytest.raises((ValueError, FileNotFoundError)):
        registry.get("missing", "1", "0" * 64)
    with pytest.raises(ValueError):
        registry.get("../outside", "1", "0" * 64)


def test_registry_loads_immutable_example_by_identity() -> None:
    campaign = CampaignPolicyV1.from_dict(with_hash(policy_dict()))
    registry = FileCampaignPolicyRegistry()
    assert registry.get("example_campaign", "1", campaign.sha256()).campaign_id == "example_campaign"


def test_registry_rejects_symlink_record(tmp_path, monkeypatch) -> None:
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "outside.json").write_text("{}")
    (campaigns / "example_campaign-v1.json").symlink_to(campaigns / "outside.json")
    monkeypatch.setattr(policy_module, "CAMPAIGNS_ROOT", campaigns)
    registry = FileCampaignPolicyRegistry()
    with pytest.raises(ValueError):
        registry.get("example_campaign", "1", "0" * 64)


def test_registry_rejects_content_modified_without_new_hash(tmp_path, monkeypatch) -> None:
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    raw = with_hash(policy_dict())
    (campaigns / "example_campaign-v1.json").write_text(json.dumps(raw))
    monkeypatch.setattr(policy_module, "CAMPAIGNS_ROOT", campaigns)
    registry = FileCampaignPolicyRegistry()
    raw["symbols"] = ["DOGEUSDT"]
    (campaigns / "example_campaign-v1.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        registry.get("example_campaign", "1", str(raw["policy_sha256"]))
