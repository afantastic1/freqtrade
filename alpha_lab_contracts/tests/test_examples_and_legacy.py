from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from alpha_lab.config import ExperimentConfig
from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyV1, FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2

ROOT = Path(__file__).parents[1]


def test_campaign_and_request_examples_validate_through_schemas_and_parsers() -> None:
    campaign_raw = json.loads((ROOT / "campaigns/example_campaign-v1.json").read_text())
    request_raw = json.loads((ROOT / "examples/research-request-v2.json").read_text())
    policy_schema = json.loads((ROOT / "schemas/campaign-policy-v1.schema.json").read_text())
    request_schema = json.loads((ROOT / "schemas/research-request-v2.schema.json").read_text())
    Draft202012Validator(policy_schema).validate(campaign_raw)
    Draft202012Validator(request_schema).validate(request_raw)

    policy = CampaignPolicyV1.from_dict(campaign_raw)
    loaded = FileCampaignPolicyRegistry().get(policy.campaign_id, policy.policy_version, policy.sha256())
    request = ResearchRequestV2.from_dict(request_raw, FileCampaignPolicyRegistry())

    assert loaded == policy
    assert request.campaign_policy_sha256 == policy.sha256()


def test_legacy_v1_config_parser_retains_existing_fields(tmp_path) -> None:
    raw = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start": "2022-01-01",
        "end": "2025-01-01",
        "horizon": 4,
        "factors": [{"name": "momentum", "params": {"periods": 4}}],
        "model": "logistic",
        "train_months": 12,
        "retrain_months": 1,
        "test_start": "2023-01-01",
        "test_end": "2025-01-01",
    }
    path = tmp_path / "legacy-v1-config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = ExperimentConfig.from_json(path)

    assert config.symbol == raw["symbol"]
    assert config.timeframe == raw["timeframe"]
    assert config.start == raw["start"]
    assert config.end == raw["end"]
    assert config.horizon == raw["horizon"]
    assert config.factors[0].name == "momentum"
    assert config.factors[0].params == {"periods": 4}
