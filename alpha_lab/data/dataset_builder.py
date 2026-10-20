"""Request-authorized time-series and cross-sectional dataset construction."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyRegistry
from alpha_lab.labels.forward_returns import forward_returns
from .availability import AvailabilityRuleRegistry, asof_join
from .build_products import (
    DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2,
)
from .dataset import MarketData
from .market_panel import MarketPanel
from alpha_lab.labels.cross_sectional import cross_sectional_labels
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
        if plan.label_extension_truncated and options.missing_data_policy == "reject":
            raise ValueError("label extension exceeds selected split end")
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
        if request.mode == "cross_sectional" and universe is None:
            raise ValueError("cross_sectional build requires an explicit universe")
        sources = self.resolver.resolve(request, plan, options)
        missing_source_issues = []
        for symbol in request.symbols:
            for data_type in request.data_types:
                identity = self.resolver._expected_identity(symbol, data_type, request.timeframe)
                if not any(source.identity == identity for source in sources):
                    if options.missing_data_policy == "reject":
                        raise ValueError(f"requested source is missing: {symbol} {data_type}")
                    missing_source_issues.append({"code": "MISSING_REQUESTED_SOURCE", "symbol": symbol, "data_type": data_type})
        if request.mode == "cross_sectional":
            return self._build_cross_sectional(request, options, plan, sources, primary, universe, missing_source_issues)
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
        for data_type in request.data_types:
            if data_type == primary:
                continue
            identity = self.resolver._expected_identity(symbol, data_type, request.timeframe)
            auxiliary = next((source for source in sources if source.identity == identity), None)
            if auxiliary is None:
                continue
            joined = asof_join(
                worker_frame[["decision_time"]], auxiliary.availability,
                decision_time="decision_time",
                max_age=(pd.Timedelta(seconds=options.max_age_by_data_type[data_type])
                         if data_type in options.max_age_by_data_type else None),
            )
            extra = joined.drop(columns=["decision_time", "available_time"], errors="ignore")
            prefix = f"{identity.market_type}_{identity.data_kind}_"
            extra = extra.rename(columns={column: prefix + column for column in extra.columns})
            worker_frame = pd.concat([worker_frame.reset_index(drop=True), extra.reset_index(drop=True)], axis=1)
        labels = forward_returns(market, horizon=request.label_horizon_bars)
        labels = labels.loc[study].copy()
        labels = labels.rename(columns={"reference_return": "label_value"})
        labels["label_valid"] = labels.label_value.notna()
        labels["validity_reason"] = labels.label_valid.map({True: "VALID", False: "INSUFFICIENT_OR_GAPPED_HORIZON"})
        worker_cols = ["open_time", "close_time", "available_time", "decision_time", "open", "high", "low", "close", "volume"]
        worker_cols.extend(column for column in worker_frame.columns
                           if column.startswith(("spot_", "futures_")) and column not in worker_cols)
        worker = WorkerInputV2(worker_frame[worker_cols].reset_index(drop=True), ("available_time",))
        evaluator = EvaluatorLabelsV1(labels.reset_index(drop=True))
        source_records = tuple({
            "dataset_id": source.identity.dataset_id(), "identity": asdict(source.identity),
            "manifest_sha256": source.manifest_sha256, "quality": source.quality,
            "availability_rule_ids": sorted(source.availability.availability_rule_id.dropna().unique().tolist()) if "availability_rule_id" in source.availability else [],
            "availability_rule_versions": sorted(source.availability.availability_rule_version.dropna().unique().tolist()) if "availability_rule_version" in source.availability else [],
            "availability_rule_sha256": sorted(source.availability.availability_rule_sha256.dropna().unique().tolist()) if "availability_rule_sha256" in source.availability else [],
            "pit_statuses": sorted(source.availability.pit_status.dropna().unique().tolist()) if "pit_status" in source.availability else [],
            "availability_evidence_sha256": sorted(source.availability.availability_evidence_sha256.dropna().unique().tolist()) if "availability_evidence_sha256" in source.availability else [],
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
            "sources": list(source_records),
            "actual_observed_intervals": {source.identity.dataset_id(): source.quality.get("observed_interval") for source in sources},
            "builder_version": "dataset-builder-v2", "mode": request.mode,
            "timeframe": request.timeframe, "symbols": list(request.symbols),
        }
        issues = tuple({"code": "MISSING_PRIMARY_SLOTS", "intervals": [stamp.isoformat() for stamp in missing_slots]}
                       for _ in [0] if len(missing_slots))
        if plan.label_extension_truncated:
            issues += ({"code": "LABEL_EXTENSION_TRUNCATED_AT_SPLIT",
                        "missing_labels": int(labels.label_value.isna().sum())},)
        report = DatasetBuildReportV1(sources=source_records, issues=tuple(missing_source_issues) + issues, quality={"worker_rows": len(worker.frame), "label_rows": len(evaluator.frame), "missing_primary_slots": len(missing_slots)})
        result = DatasetBuildResultV2(worker, evaluator, report, manifest)
        artifact = publish_dataset(result, self.output_root)
        return DatasetBuildResultV2(worker, evaluator, report, manifest, artifact)

    def _build_cross_sectional(self, request, options, plan, sources, primary, universe, missing_source_issues=()):
        step = pd.Timedelta(seconds=timeframe_to_seconds(request.timeframe))
        assets = []
        by_symbol = {}
        missing_slots_by_symbol = {}
        for symbol in request.symbols:
            identity = self.resolver._expected_identity(symbol, primary, request.timeframe)
            source = next((item for item in sources if item.identity == identity), None)
            if source is None:
                raise ValueError(f"primary market source missing for {symbol}")
            if source.availability.pit_status.eq("UNVERIFIED").any():
                raise ValueError("UNVERIFIED availability cannot enter worker input")
            frame = source.availability.copy(deep=True)
            frame["open_time"] = pd.to_datetime(frame.date, utc=True)
            frame["close_time"] = frame.open_time + step
            frame["available_time"] = pd.to_datetime(frame.available_time, utc=True)
            frame["decision_time"] = frame.available_time
            for data_type in request.data_types:
                if data_type == primary:
                    continue
                aux_identity = self.resolver._expected_identity(symbol, data_type, request.timeframe)
                auxiliary = next((item for item in sources if item.identity == aux_identity), None)
                if auxiliary is None:
                    continue
                joined = asof_join(
                    frame[["decision_time"]], auxiliary.availability,
                    decision_time="decision_time",
                    max_age=(pd.Timedelta(seconds=options.max_age_by_data_type[data_type])
                             if data_type in options.max_age_by_data_type else None),
                )
                extra = joined.drop(columns=["decision_time", "available_time"], errors="ignore")
                prefix = f"{aux_identity.market_type}_{aux_identity.data_kind}_"
                frame = pd.concat([frame.reset_index(drop=True), extra.rename(columns={column: prefix + column for column in extra.columns}).reset_index(drop=True)], axis=1)
            market = MarketData(symbol, request.timeframe, frame[[
                "open_time", "close_time", "available_time", "open", "high", "low", "close", "volume",
            ] + [column for column in frame.columns if column.startswith(("spot_", "futures_"))]].sort_values("open_time").reset_index(drop=True))
            assets.append(market)
            by_symbol[symbol] = source
        panel = MarketPanel.from_assets(assets)
        expected_slots = pd.date_range(plan.warmup_start, plan.label_extension_end, freq=step, inclusive="left")
        for symbol in request.symbols:
            actual = panel.frame.xs(symbol, level="symbol").index
            missing = enforce_required_slots(expected_slots, pd.DatetimeIndex(actual), options.missing_data_policy)
            if len(missing):
                missing_slots_by_symbol[symbol] = [stamp.isoformat() for stamp in missing]
        study_mask = ((panel.frame.index.get_level_values("open_time") >= pd.Timestamp(request.start))
                      & (panel.frame.index.get_level_values("open_time") < pd.Timestamp(request.end)))
        worker_mask = ((panel.frame.index.get_level_values("open_time") >= pd.Timestamp(plan.warmup_start))
                       & (panel.frame.index.get_level_values("open_time") < pd.Timestamp(request.end)))
        membership = universe.membership(panel)
        if not membership.index.equals(panel.frame.index) or membership.isna().any():
            raise ValueError("universe membership must align to market panel")
        missing_membership_symbols = []
        if type(universe).__name__ == "HistoricalUniverse":
            known = set(universe.listings.symbol)
            missing_membership_symbols = sorted(set(request.symbols) - known)
        if missing_membership_symbols and options.missing_data_policy == "reject":
            raise ValueError("missing point-in-time membership history")
        labels = cross_sectional_labels(
            panel, membership, timeframe=request.timeframe, horizon=request.label_horizon_bars
        )
        label_availability = labels["label_available_time"].where(membership.astype(bool))
        max_availability = label_availability.groupby(level="open_time").transform("max")
        labels["label_available_time"] = max_availability.where(labels.relative_return.notna())
        labels = labels.loc[study_mask].copy()
        labels["label_value"] = labels["relative_return"]
        labels["label_end_time"] = pd.Series(labels.index.get_level_values("open_time"), index=labels.index) + request.label_horizon_bars * step
        labels.loc[~labels.label_valid if "label_valid" in labels else labels.relative_return.isna(), "label_end_time"] = pd.NaT
        labels["label_valid"] = labels.label_value.notna()
        labels["validity_reason"] = labels.label_valid.map({True: "VALID", False: "INSUFFICIENT_OR_GAPPED_HORIZON_OR_MEMBERSHIP"})
        if options.missing_data_policy == "reject" and not labels.label_valid.all():
            raise ValueError("missing or invalid cross-sectional labels under reject policy")
        worker_frame = panel.frame.loc[worker_mask].copy()
        worker_frame["eligible"] = membership.loc[worker_frame.index]
        worker_frame["decision_time"] = worker_frame.available_time
        worker_frame = worker_frame.reset_index()
        worker = WorkerInputV2(worker_frame, ("available_time",))
        evaluator = EvaluatorLabelsV1(labels.reset_index())
        source_records = tuple({
            "dataset_id": source.identity.dataset_id(), "identity": asdict(source.identity),
            "manifest_sha256": source.manifest_sha256, "quality": source.quality,
            "availability_rule_ids": sorted(source.availability.availability_rule_id.dropna().unique().tolist()) if "availability_rule_id" in source.availability else [],
            "availability_rule_versions": sorted(source.availability.availability_rule_version.dropna().unique().tolist()) if "availability_rule_version" in source.availability else [],
            "availability_rule_sha256": sorted(source.availability.availability_rule_sha256.dropna().unique().tolist()) if "availability_rule_sha256" in source.availability else [],
            "pit_statuses": sorted(source.availability.pit_status.dropna().unique().tolist()) if "pit_status" in source.availability else [],
            "availability_evidence_sha256": sorted(source.availability.availability_evidence_sha256.dropna().unique().tolist()) if "availability_evidence_sha256" in source.availability else [],
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
            "sources": list(source_records),
            "actual_observed_intervals": {source.identity.dataset_id(): source.quality.get("observed_interval") for source in sources},
            "builder_version": "dataset-builder-v2", "mode": request.mode,
            "timeframe": request.timeframe, "symbols": list(request.symbols),
            "universe_hash": universe.universe_hash,
            "membership_sha256": hashlib.sha256(membership.astype("uint8").to_numpy().tobytes()).hexdigest(),
            "survivorship_caveat": "fixed universe uses current membership" if type(universe).__name__ == "FixedUniverse" else None,
        }
        cs_issues = (tuple(missing_source_issues)
                     + tuple({"code": "MISSING_PRIMARY_SLOTS", "symbol": symbol, "intervals": intervals}
                             for symbol, intervals in missing_slots_by_symbol.items())
                     + (({"code": "MISSING_MEMBERSHIP_HISTORY", "symbols": missing_membership_symbols},)
                        if missing_membership_symbols else ())
                     + (({"code": "LABEL_EXTENSION_TRUNCATED_AT_SPLIT",
                           "missing_labels": int(labels.label_value.isna().sum())},)
                        if plan.label_extension_truncated else ()))
        report = DatasetBuildReportV1(sources=source_records, issues=cs_issues, quality={
            "worker_rows": len(worker.frame), "label_rows": len(evaluator.frame),
            "membership_count": int(membership.loc[study_mask].sum()),
        })
        result = DatasetBuildResultV2(worker, evaluator, report, manifest)
        artifact = publish_dataset(result, self.output_root)
        return DatasetBuildResultV2(worker, evaluator, report, manifest, artifact)
