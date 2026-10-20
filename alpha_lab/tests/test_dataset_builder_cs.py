import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab.data.availability import AvailabilityRuleRegistry
from alpha_lab.data.dataset_builder import DatasetBuilderV2
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab.data.universe import FixedUniverse, HistoricalUniverse
from data_engineering.sources.contracts import DatasetIdentity


class Resolver:
    def __init__(self, symbols):
        self.symbols = symbols
        self.sources = []
        for symbol, asset in symbols.items():
            identity = DatasetIdentity("binance", "futures", asset, f"{asset}/USDT:USDT", "ohlcv", "5m")
            dates = pd.date_range("2023-01-01T00:00Z", periods=8, freq="5min")
            obs = pd.DataFrame({"date": dates, "open": [100., 101., 102., 103., 104., 105., 106., 107.], "high": [101., 102., 103., 104., 105., 106., 107., 108.], "low": [99., 100., 101., 102., 103., 104., 105., 106.], "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5], "volume": [1.] * 8})
            available = obs.copy()
            available["event_time"] = available.date
            available["available_time"] = available.date + pd.Timedelta(minutes=5, seconds=1)
            available["pit_status"] = "ASSUMED"
            self.sources.append(SimpleNamespace(identity=identity, manifest_sha256=("a" if asset == "BTC" else "b") * 64, observations=obs, availability=available, quality={"status": "AVAILABLE"}))

    def _expected_identity(self, symbol, data_type, timeframe):
        source = next(s for s in self.sources if s.identity.asset == self.symbols[symbol])
        return source.identity

    def resolve(self, request, plan, options):
        return tuple(self.sources)


def make_request():
    policy_raw = json.loads((Path(__file__).parents[2] / "alpha_lab_contracts/campaigns/example_campaign-v1.json").read_text())
    raw = {"protocol_version": "2.0-research-request", "request_id": "cs-test", "campaign_id": "example_campaign", "policy_version": "1", "campaign_policy_sha256": policy_raw["policy_sha256"], "start": "2023-01-01T00:10:00Z", "end": "2023-01-01T00:25:00Z", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "5m", "mode": "cross_sectional", "data_types": ["futures_ohlcv"], "split": "discovery", "lookback_bars": 2, "label_horizon_bars": 2, "embargo_bars": 0}
    registry = FileCampaignPolicyRegistry()
    return ResearchRequestV2.from_dict(raw, registry), registry


def builder(tmp_path):
    request, policy = make_request()
    resolver = Resolver({"BTCUSDT": "BTC", "ETHUSDT": "ETH"})
    return DatasetBuilderV2(policy, resolver, AvailabilityRuleRegistry({}), tmp_path), request


def test_cs_membership_aligns_to_market_panel(tmp_path):
    build, request = builder(tmp_path)
    result = build.build(request, options=DatasetBuildOptionsV1("preserve", None, {}, {}, {}), universe=FixedUniverse("both", ("BTCUSDT", "ETHUSDT")))
    worker = result.worker_input.frame
    assert set(worker.symbol) == {"BTCUSDT", "ETHUSDT"}
    assert worker.eligible.all()
    labels = result.evaluator_labels.frame
    assert {"symbol", "open_time", "label_value", "label_available_time"}.issubset(labels.columns)


def test_cs_uses_point_in_time_historical_membership(tmp_path):
    build, request = builder(tmp_path)
    universe = HistoricalUniverse("history", pd.DataFrame({"symbol": ["BTCUSDT", "ETHUSDT"], "eligible_from": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T00:20Z"], utc=True)}))
    result = build.build(request, options=DatasetBuildOptionsV1("preserve", None, {}, {}, {}), universe=universe)
    worker = result.worker_input.frame
    eth = worker.loc[worker.symbol.eq("ETHUSDT")]
    assert not eth.loc[eth.open_time < pd.Timestamp("2023-01-01T00:20Z"), "eligible"].any()
    assert eth.loc[eth.open_time >= pd.Timestamp("2023-01-01T00:20Z"), "eligible"].all()


def test_cs_requires_explicit_universe(tmp_path):
    build, request = builder(tmp_path)
    with pytest.raises(ValueError, match="explicit universe"):
        build.build(request, options=DatasetBuildOptionsV1("preserve", None, {}, {}, {}))


def test_cs_manifest_fingerprints_membership(tmp_path):
    build, request = builder(tmp_path)
    result = build.build(request, options=DatasetBuildOptionsV1("preserve", None, {}, {}, {}), universe=FixedUniverse("one", ("BTCUSDT",)))
    assert result.manifest["universe_hash"]
    assert result.manifest["membership_sha256"]
    assert "survivorship_caveat" in result.manifest


def test_missing_membership_follows_build_policy(tmp_path):
    build, request = builder(tmp_path)
    universe = HistoricalUniverse("partial", pd.DataFrame({"symbol": ["BTCUSDT"], "eligible_from": pd.to_datetime(["2023-01-01T00:00Z"], utc=True)}))
    result = build.build(request, options=DatasetBuildOptionsV1("preserve", None, {}, {}, {}), universe=universe)
    assert result.report.issues[0]["code"] == "MISSING_MEMBERSHIP_HISTORY"
    with pytest.raises(ValueError, match="membership history"):
        build.build(request, options=DatasetBuildOptionsV1("reject", None, {}, {}, {}), universe=universe)
