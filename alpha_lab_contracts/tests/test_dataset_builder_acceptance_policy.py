import json

from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry


def test_dataset_builder_acceptance_policy_is_valid():
    path = __import__("pathlib").Path(__file__).parents[1] / "campaigns/dataset_builder_acceptance-v1.json"
    raw = json.loads(path.read_text())
    policy = FileCampaignPolicyRegistry().get(raw["campaign_id"], raw["policy_version"], raw["policy_sha256"])
    assert set(policy.symbols) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}
    assert policy.splits["holdout"].start.isoformat() == "2026-01-01T00:00:00+00:00"
