import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab.data.availability import AvailabilityRuleRegistry, AvailabilityRuleV1
from alpha_lab.data.dataset_builder import DatasetBuilderV2
from alpha_lab.data.source_resolver import DatasetSourceResolver
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from data_engineering.freqtrade_feather import register_feather_file
from data_engineering.sources.contracts import DatasetIdentity


def setup_build(tmp_path, changed_future=False, request_start="2023-01-01T00:10:00Z", request_end="2023-01-01T00:20:00Z", acceptance=False):
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    catalog_root = tmp_path / "catalog"
    output_root = tmp_path / "output"
    dates = pd.date_range("2023-01-01T00:00:00Z", periods=8, freq="5min")
    frame = pd.DataFrame({"date": dates, "open": [100., 101., 102., 103., 104., 105., 106., 107.], "high": [101., 102., 103., 104., 105., 106., 107., 108.], "low": [99., 100., 101., 102., 103., 104., 105., 106.], "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5], "volume": [1.] * 8})
    if changed_future:
        frame.loc[6:, "close"] += 50
        frame.loc[6:, "high"] += 50
    path = source_root / "BTC_USDT_USDT-5m-futures.feather"
    frame.to_feather(path)
    registered = register_feather_file(catalog_root, source_root_id="binance-data", source_root=source_root, relative_path=Path(path.name), exchange="binance", market_type="futures", asset="BTC", exchange_symbol="BTC/USDT:USDT", data_kind="ohlcv", timeframe="5m", source="local")
    identity = DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "5m")
    registry = AvailabilityRuleRegistry({"close-delay": AvailabilityRuleV1("close-delay", identity, "fixed_delay", "date", fixed_delay=pd.Timedelta(seconds=1).to_pytimedelta())})
    resolver = DatasetSourceResolver(catalog_root, {"binance-data": source_root}, exchange="binance", symbol_identities={"BTCUSDT": {"exchange": "binance", "market_type": "futures", "asset": "BTC", "exchange_symbol": "BTC/USDT:USDT"}}, availability_registry=registry)
    campaign_filename = "dataset_builder_acceptance-v1.json" if acceptance else "example_campaign-v1.json"
    policy_raw = json.loads((Path(__file__).parents[2] / "alpha_lab_contracts/campaigns" / campaign_filename).read_text())
    request_raw = {"protocol_version": "2.0-research-request", "request_id": "ts-test", "campaign_id": policy_raw["campaign_id"], "policy_version": "1", "campaign_policy_sha256": policy_raw["policy_sha256"], "start": request_start, "end": request_end, "symbols": ["BTCUSDT"], "timeframe": "5m", "mode": "time_series", "data_types": ["futures_ohlcv"], "split": "discovery", "lookback_bars": 2, "label_horizon_bars": 2, "embargo_bars": 0}
    policy_registry = FileCampaignPolicyRegistry()
    request = ResearchRequestV2.from_dict(request_raw, policy_registry)
    options = DatasetBuildOptionsV1("preserve", None, {}, {"futures_ohlcv": "close-delay"}, {})
    builder = DatasetBuilderV2(policy_registry, resolver, registry, output_root)
    return builder, request, options, registered


def test_builder_ts_constructs_btc_dataset_from_request(tmp_path):
    builder, request, options, _ = setup_build(tmp_path)
    result = builder.build(request, options=options)
    assert result.artifact is not None
    assert len(result.worker_input.frame) == 4
    assert len(result.evaluator_labels.frame) == 2
    source = result.manifest["sources"][0]
    assert source["manifest_sha256"]
    assert source["availability_rule_sha256"]
    assert source["quality"]["selected_partitions"]


def test_builder_ts_separates_future_labels(tmp_path):
    builder, request, options, _ = setup_build(tmp_path)
    result = builder.build(request, options=options)
    assert not any("label" in name.lower() for name in result.worker_input.frame.columns)
    assert {"label_value", "label_end_time", "label_available_time"}.issubset(result.evaluator_labels.frame.columns)


def test_future_perturbation_does_not_change_past_worker_input(tmp_path):
    first_builder, request, options, _ = setup_build(tmp_path / "first")
    second_builder, _, _, _ = setup_build(tmp_path / "second", changed_future=True)
    first = first_builder.build(request, options=options).worker_input.asof(pd.Timestamp("2023-01-01T00:15:01Z"))
    second = second_builder.build(request, options=options).worker_input.asof(pd.Timestamp("2023-01-01T00:15:01Z"))
    pd.testing.assert_frame_equal(first, second)


def test_builder_ts_rejects_label_extension_past_split(tmp_path):
    builder, request, options, _ = setup_build(tmp_path, request_start="2023-12-31T23:50:00Z", request_end="2023-12-31T23:55:00Z")
    options = replace(options, missing_data_policy="reject")
    with pytest.raises(ValueError, match="label extension exceeds"):
        builder.build(request, options=options)


def test_builder_ts_rejects_wholly_missing_primary_source_in_preserve_mode(tmp_path):
    builder, request, options, _ = setup_build(tmp_path)
    original = builder.resolver
    resolved = original.resolve(request, __import__("alpha_lab.data.temporal_plan", fromlist=["build_temporal_plan"]).build_temporal_plan(request, builder.policy_registry.get(request.campaign_id, request.policy_version, request.campaign_policy_sha256)), options)[0]
    empty = type("Resolved", (), {"identity": resolved.identity, "manifest_sha256": resolved.manifest_sha256, "observations": resolved.observations.iloc[0:0], "availability": resolved.availability.iloc[0:0], "quality": resolved.quality})()
    class Resolver:
        def _expected_identity(self, *args):
            return original._expected_identity(*args)
        def resolve(self, *args):
            return (empty,)
    builder.resolver = Resolver()
    with pytest.raises(ValueError, match="no observations"):
        builder.build(request, options=options)


@pytest.mark.parametrize(
    ("data_type", "data_kind", "value_column", "feature"),
    [
        ("funding_rate", "funding_rate", "funding_rate", "futures_funding_rate_funding_rate"),
        ("open_interest", "open_interest", "open_interest_amount", "futures_open_interest_open_interest_amount"),
        ("spot_ohlcv", "ohlcv", "close", "spot_ohlcv_close"),
    ],
)
def test_builder_ts_auxiliary_events_join_on_availability_time(tmp_path, data_type, data_kind, value_column, feature):
    builder, request, options, _ = setup_build(tmp_path, acceptance=(data_type in {"open_interest", "spot_ohlcv"}))
    if data_type == "spot_ohlcv":
        options = replace(options, primary_data_type="futures_ohlcv")
    original = builder.resolver
    primary = original.resolve(request, __import__("alpha_lab.data.temporal_plan", fromlist=["build_temporal_plan"]).build_temporal_plan(request, builder.policy_registry.get(request.campaign_id, request.policy_version, request.campaign_policy_sha256)), options)[0]
    request = replace(request, data_types=("futures_ohlcv", data_type))
    event_identity = DatasetIdentity("binance", "spot", "BTC", "BTC/USDT", "ohlcv", "5m") if data_type == "spot_ohlcv" else DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", data_kind, "5m")
    event_values = {"event_time": pd.to_datetime(["2023-01-01T00:19:00Z"], utc=True), "available_time": pd.to_datetime(["2023-01-01T00:20:01Z"], utc=True), "pit_status": ["EVIDENCED"], value_column: [0.001]}
    if data_type == "spot_ohlcv":
        event_values.update({"open": [1.0], "high": [2.0], "low": [0.5], "volume": [1.0]})
    events = pd.DataFrame(event_values)
    auxiliary = type("Resolved", (), {"identity": event_identity, "manifest_sha256": "c" * 64, "observations": events, "availability": events, "quality": {"status": "AVAILABLE"}})()
    class Resolver:
        def _expected_identity(self, *args):
            if args[1] == "spot_ohlcv":
                return event_identity
            if args[1] == data_type:
                return event_identity
            return original._expected_identity(*args)
        def resolve(self, *args):
            return primary, auxiliary
    builder.resolver = Resolver()
    result = builder.build(request, options=options)
    worker = result.worker_input.frame
    study = worker.loc[worker.open_time >= pd.Timestamp(request.start)]
    assert pd.isna(study.iloc[0][feature])
    assert study.iloc[1][feature] == 0.001
