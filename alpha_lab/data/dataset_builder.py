"""Request-authorized time-series and cross-sectional dataset construction."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyRegistry
from alpha_lab.labels.forward_returns import forward_returns
from .availability import AvailabilityRuleRegistry
from .build_products import (
    DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2,
)
from .dataset import MarketData
from .dataset_manifest import publish_dataset
from .source_resolver import DatasetSourceResolver
from .temporal_plan import DatasetBuildOptionsV1, build_temporal_plan, enforce_required_slots
from freqtrade.exchange import timeframe_to_seconds


class DatasetBuilderV2:
    def __init__(self, policy_registry: CampaignPolicyRegistry, resolver: DatasetSourceResolver,
                 availability_registry: AvailabilityRuleRegistry, output_root: Path):
        self.policy_registry = policy_registry
        self.resolver = resolver
        self.availability_registry = availability_registry
        self.output_root = Path(output_root)

    def build(self, request: ResearchRequestV2, *, options: DatasetBuildOptionsV1,
              universe=None) -> DatasetBuildResultV2:
        policy = self.policy_registry.get(request.campaign_id, request.policy_version, request.campaign_policy_sha256)
        request = ResearchRequestV2.from_dict(json.loads(request.canonical_json()), self.policy_registry)
        plan = build_temporal_plan(request, policy)
        ohlcv = [item for item in request.data_types if item in {"futures_ohlcv", "spot_ohlcv"}]
        primary = options.primary_data_type
        if primary is None:
            if len(ohlcv) != 1:
                raise ValueError("primary_data_type is required for ambiguous or event-only requests")
            primary = ohlcv[0]
        if primary not in ohlcv:
            raise ValueError("primary_data_type must identify a requested OHLCV source")
        if request.mode == "time_series" and len(request.symbols) != 1:
            raise ValueError("time_series build requires exactly one symbol")
        sources = self.resolver.resolve(request, plan, options)
        symbol = request.symbols[0]
        primary_source = next((source for source in sources if source.identity == self.resolver._expected_identity(symbol, primary, request.timeframe)), None)
        if primary_source is None:
            raise ValueError("primary market source is missing")
        if primary_source.availability.pit_status.eq("UNVERIFIED").any():
            raise ValueError("UNVERIFIED availability cannot enter worker input")
        step = pd.Timedelta(seconds=timeframe_to_seconds(request.timeframe))
        market_frame = primary_source.availability.copy(deep=True)
        market_frame["open_time"] = pd.to_datetime(market_frame["date"], utc=True)
        market_frame["close_time"] = market_frame.open_time + step
        market_frame["available_time"] = pd.to_datetime(market_frame.available_time, utc=True)
        market_frame = market_frame.sort_values("open_time").reset_index(drop=True)
        expected_slots = pd.date_range(
            plan.warmup_start, plan.label_extension_end, freq=step, inclusive="left"
        )
        missing_slots = enforce_required_slots(
            expected_slots, pd.DatetimeIndex(market_frame.open_time), options.missing_data_policy
        )
        market = MarketData(symbol, request.timeframe, market_frame[[
            "open_time", "close_time", "available_time", "open", "high", "low", "close", "volume",
        ]])
        study = (market.frame.open_time >= pd.Timestamp(request.start)) & (market.frame.open_time < pd.Timestamp(request.end))
        worker_mask = (market.frame.open_time >= pd.Timestamp(plan.warmup_start)) & (market.frame.open_time < pd.Timestamp(request.end))
        worker_frame = market.frame.loc[worker_mask].copy()
        worker_frame["decision_time"] = worker_frame.available_time
        labels = forward_returns(market, horizon=request.label_horizon_bars)
        labels = labels.loc[study].copy()
        labels = labels.rename(columns={"reference_return": "label_value"})
        labels["label_valid"] = labels.label_value.notna()
        labels["validity_reason"] = labels.label_valid.map({True: "VALID", False: "INSUFFICIENT_OR_GAPPED_HORIZON"})
        worker_cols = ["open_time", "close_time", "available_time", "decision_time", "open", "high", "low", "close", "volume"]
        worker = WorkerInputV2(worker_frame[worker_cols].reset_index(drop=True), ("available_time",))
        evaluator = EvaluatorLabelsV1(labels.reset_index(drop=True))
        source_records = tuple({
            "dataset_id": source.identity.dataset_id(), "identity": asdict(source.identity),
            "manifest_sha256": source.manifest_sha256, "quality": source.quality,
        } for source in sources)
        manifest = {
            "schema_version": "alpha-lab-dataset-manifest-v1",
            "request": json.loads(request.canonical_json()), "request_sha256": request.sha256(),
            "policy_id": request.campaign_id, "policy_version": request.policy_version,
            "policy_sha256": request.campaign_policy_sha256,
            "split": request.split, "study_bounds": [request.start.isoformat(), request.end.isoformat()],
            "warmup_bounds": [plan.warmup_start.isoformat(), plan.warmup_end.isoformat()],
            "label_extension_bounds": [plan.label_extension_start.isoformat(), plan.label_extension_end.isoformat()],
            "missing_data_policy": options.missing_data_policy,
            "source_pins": {source.identity.dataset_id(): source.manifest_sha256 for source in sources},
            "builder_version": "dataset-builder-v2", "mode": request.mode,
            "timeframe": request.timeframe, "symbols": list(request.symbols),
        }
        issues = tuple({"code": "MISSING_PRIMARY_SLOTS", "intervals": [stamp.isoformat() for stamp in missing_slots]}
                       for _ in [0] if len(missing_slots))
        report = DatasetBuildReportV1(sources=source_records, issues=issues, quality={"worker_rows": len(worker.frame), "label_rows": len(evaluator.frame), "missing_primary_slots": len(missing_slots)})
        result = DatasetBuildResultV2(worker, evaluator, report, manifest)
        artifact = publish_dataset(result, self.output_root)
        return DatasetBuildResultV2(worker, evaluator, report, manifest, artifact)
