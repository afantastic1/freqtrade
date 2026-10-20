import json
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


def setup_build(tmp_path, changed_future=False, request_start="2023-01-01T00:10:00Z", request_end="2023-01-01T00:20:00Z"):
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
    policy_raw = json.loads((Path(__file__).parents[2] / "alpha_lab_contracts/campaigns/example_campaign-v1.json").read_text())
    request_raw = {"protocol_version": "2.0-research-request", "request_id": "ts-test", "campaign_id": "example_campaign", "policy_version": "1", "campaign_policy_sha256": policy_raw["policy_sha256"], "start": request_start, "end": request_end, "symbols": ["BTCUSDT"], "timeframe": "5m", "mode": "time_series", "data_types": ["futures_ohlcv"], "split": "discovery", "lookback_bars": 2, "label_horizon_bars": 2, "embargo_bars": 0}
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
    with pytest.raises(ValueError, match="label extension exceeds"):
        builder.build(request, options=options)
