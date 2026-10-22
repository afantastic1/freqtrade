"""Request-authorized time-series and cross-sectional dataset construction."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyRegistry
from .availability import AvailabilityRuleRegistry, asof_join
from .build_products import (
    DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2,
)
from .dataset import MarketData
from .market_panel import MarketPanel
from .dataset_manifest import publish_dataset
from .source_resolver import DatasetSourceResolver
from .temporal_plan import DatasetBuildOptionsV1, build_temporal_plan, enforce_required_slots
from .pit_labels import (
    cross_sectional_labels_v2, dynamic_membership_asof, forward_returns_v2,
    historical_membership_asof,
)
from .universe import DynamicUniverse, HistoricalUniverse
from alpha_lab.data.timeframe import timeframe_to_seconds


def _effective_max_age(source, data_type, options):
    configured = options.max_age_by_data_type.get(data_type)
    registered = source.quality.get("availability_max_age_seconds")
    values = [pd.Timedelta(seconds=value) for value in (configured, registered) if value is not None]
    return min(values) if values else None


def _missing_decisions(source, request, step):
    # Only fixed-delay rules define a regular expected decision schedule.
    delay_seconds = source.quality.get("availability_fixed_delay_seconds")
    if delay_seconds is None:
        return pd.DatetimeIndex([])
    delay = pd.Timedelta(seconds=delay_seconds)
    first_open = (pd.Timestamp(request.start) - step - delay).ceil(step)
    opens = pd.date_range(first_open, pd.Timestamp(request.end), freq=step, inclusive="left")
    expected = opens + step + delay
    expected = expected[(expected >= pd.Timestamp(request.start)) & (expected < pd.Timestamp(request.end))]
    observed = pd.DatetimeIndex(pd.to_datetime(source.availability.available_time, utc=True).dropna().unique())
    return expected.difference(observed)


def _add_spot_perp_basis(frame: pd.DataFrame, primary: str, data_types) -> pd.DataFrame:
    """Add PIT-aligned premium/discount columns when both markets were requested."""
    if not {"spot_ohlcv", "futures_ohlcv"}.issubset(data_types):
        return frame
    result = frame.copy(deep=True)
    spot = "close" if primary == "spot_ohlcv" else "spot_ohlcv_close"
    perp = "close" if primary == "futures_ohlcv" else "futures_ohlcv_close"
    spot_available = (result.get("spot_ohlcv_is_available", True)
                      if primary != "spot_ohlcv" else result["close"].notna())
    perp_available = (result.get("futures_ohlcv_is_available", True)
                      if primary != "futures_ohlcv" else result["close"].notna())
    valid = result[spot].notna() & result[perp].notna() & spot_available & perp_available
    result["spot_perp_basis"] = ((result[perp] / result[spot]) - 1.0).where(valid)
    result["spot_perp_basis_absolute"] = (result[perp] - result[spot]).where(valid)
    result["spot_perp_basis_is_available"] = valid.astype(bool)
    return result


def _worker_value_columns(identity, observations: pd.DataFrame | None = None) -> tuple[str, ...]:
    if identity.data_kind == "ohlcv":
        return ("open", "high", "low", "close", "volume")
    if identity.data_kind == "open_interest":
        available = set(observations.columns) if observations is not None else set()
        return tuple(name for name in ("open_interest_amount", "open_interest_value")
                     if name in available) or ("open_interest_amount", "open_interest_value")
    if identity.data_kind == "funding_rate":
        return ("funding_rate",)
    if identity.data_kind == "mark_price":
        return ("mark_price",)
    raise ValueError("data kind has no registered Worker value-column contract")


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
        required_history_bars = (universe.min_listing_bars
                                 if isinstance(universe, DynamicUniverse) else 0)
        plan = build_temporal_plan(request, policy, required_history_bars=required_history_bars)
        if plan.warmup_truncated_bars and options.missing_data_policy == "reject":
            raise ValueError(f"warmup exceeds selected split by {plan.warmup_truncated_bars} bars")
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
        if primary_source.observations.empty:
            raise ValueError("primary market source has no observations in the planned interval")
        if primary_source.availability.pit_status.eq("UNVERIFIED").any():
            raise ValueError("UNVERIFIED availability cannot enter worker input")
        step = pd.Timedelta(seconds=timeframe_to_seconds(request.timeframe))
        market_frame = primary_source.availability.copy(deep=True)
        market_frame["open_time"] = pd.to_datetime(market_frame["date"], utc=True)
        market_frame["close_time"] = market_frame.open_time + step
        market_frame["available_time"] = pd.to_datetime(market_frame.available_time, utc=True)
        market_frame = market_frame.sort_values("open_time").reset_index(drop=True)
        expected_slots = pd.date_range(
            pd.Timestamp(plan.warmup_start).ceil(step), plan.label_extension_end,
            freq=step, inclusive="left"
        )
        missing_slots = enforce_required_slots(
            expected_slots, pd.DatetimeIndex(market_frame.open_time), options.missing_data_policy
        )
        market = MarketData(symbol, request.timeframe, market_frame[[
            "open_time", "close_time", "available_time", "open", "high", "low", "close", "volume",
        ]])
        study = (market.frame.available_time >= pd.Timestamp(request.start)) & (market.frame.available_time < pd.Timestamp(request.end))
        worker_mask = ((market.frame.open_time >= pd.Timestamp(plan.warmup_start))
                       & (market.frame.available_time < pd.Timestamp(request.end)))
        worker_frame = market.frame.loc[worker_mask].copy()
        worker_frame["decision_time"] = worker_frame.available_time
        auxiliary_issues = []
        missing_decisions = _missing_decisions(primary_source, request, step)
        if len(missing_decisions) and options.missing_data_policy == "reject":
            raise ValueError(f"missing required decision slots: {len(missing_decisions)}")
        for data_type in request.data_types:
            if data_type == primary:
                continue
            identity = self.resolver._expected_identity(symbol, data_type, request.timeframe)
            auxiliary = next((source for source in sources if source.identity == identity), None)
            if auxiliary is None:
                prefix = f"{identity.market_type}_{identity.data_kind}_"
                columns = _worker_value_columns(identity)
                for column in columns:
                    worker_frame[prefix + column] = pd.NA
                worker_frame[prefix + "is_available"] = False
                continue
            joined = asof_join(
                worker_frame[["decision_time"]], auxiliary.availability,
                decision_time="decision_time",
                max_age=_effective_max_age(auxiliary, data_type, options),
            )
            source_available = joined.available_time.notna()
            if not source_available.all():
                auxiliary_issues.append({"code": "AUXILIARY_UNAVAILABLE_AT_DECISION",
                                         "symbol": symbol, "data_type": data_type,
                                         "decision_rows": int((~source_available).sum())})
            if options.missing_data_policy == "reject" and not source_available.all():
                raise ValueError(f"auxiliary source has unavailable or stale rows: {data_type}")
            extra = joined.loc[:, list(_worker_value_columns(identity, auxiliary.observations))].copy()
            prefix = f"{identity.market_type}_{identity.data_kind}_"
            extra = extra.rename(columns={column: prefix + column for column in extra.columns})
            extra[prefix + "is_available"] = source_available.to_numpy()
            worker_frame = pd.concat([worker_frame.reset_index(drop=True), extra.reset_index(drop=True)], axis=1)
        worker_frame = _add_spot_perp_basis(worker_frame, primary, request.data_types)
        labels = forward_returns_v2(market, horizon=request.label_horizon_bars)
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
        if plan.warmup_truncated_bars:
            issues += ({"code": "WARMUP_TRUNCATED_AT_SPLIT",
                        "missing_warmup_bars": plan.warmup_truncated_bars},)
        if len(missing_decisions):
            issues += ({"code": "MISSING_DECISION_SLOTS",
                        "decision_times": [stamp.isoformat() for stamp in missing_decisions]},)
        report = DatasetBuildReportV1(sources=source_records, issues=tuple(missing_source_issues) + tuple(auxiliary_issues) + issues, quality={"worker_rows": len(worker.frame), "label_rows": len(evaluator.frame), "missing_primary_slots": len(missing_slots)})
        result = DatasetBuildResultV2(worker, evaluator, report, manifest)
        artifact = publish_dataset(result, self.output_root)
        return DatasetBuildResultV2(worker, evaluator, report, manifest, artifact)

    def _build_cross_sectional(self, request, options, plan, sources, primary, universe, missing_source_issues=()):
        step = pd.Timedelta(seconds=timeframe_to_seconds(request.timeframe))
        assets = []
        by_symbol = {}
        missing_slots_by_symbol = {}
        auxiliary_issues = []
        for symbol in request.symbols:
            identity = self.resolver._expected_identity(symbol, primary, request.timeframe)
            source = next((item for item in sources if item.identity == identity), None)
            if source is None:
                raise ValueError(f"primary market source missing for {symbol}")
            if source.observations.empty:
                raise ValueError(f"primary market source has no observations for {symbol}")
            if source.availability.pit_status.eq("UNVERIFIED").any():
                raise ValueError("UNVERIFIED availability cannot enter worker input")
            missing_decisions = _missing_decisions(source, request, step)
            if len(missing_decisions) and options.missing_data_policy == "reject":
                raise ValueError(f"missing required decision slots for {symbol}: {len(missing_decisions)}")
            if len(missing_decisions):
                auxiliary_issues.append({"code": "MISSING_DECISION_SLOTS", "symbol": symbol,
                                         "decision_times": [stamp.isoformat() for stamp in missing_decisions]})
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
                    prefix = f"{aux_identity.market_type}_{aux_identity.data_kind}_"
                    columns = _worker_value_columns(aux_identity)
                    for column in columns:
                        frame[prefix + column] = pd.NA
                    frame[prefix + "is_available"] = False
                    continue
                joined = asof_join(
                    frame[["decision_time"]], auxiliary.availability,
                    decision_time="decision_time",
                    max_age=_effective_max_age(auxiliary, data_type, options),
                )
                source_available = joined.available_time.notna()
                required_rows = frame.decision_time < pd.Timestamp(request.end)
                required_available = source_available & required_rows
                if not required_available.loc[required_rows].all():
                    auxiliary_issues.append({"code": "AUXILIARY_UNAVAILABLE_AT_DECISION",
                                             "symbol": symbol, "data_type": data_type,
                                             "decision_rows": int((~source_available & required_rows).sum())})
                if options.missing_data_policy == "reject" and not required_available.loc[required_rows].all():
                    raise ValueError(f"auxiliary source has unavailable or stale rows: {data_type}")
                extra = joined.loc[:, list(_worker_value_columns(aux_identity, auxiliary.observations))].copy()
                prefix = f"{aux_identity.market_type}_{aux_identity.data_kind}_"
                extra = extra.rename(columns={column: prefix + column for column in extra.columns})
                extra[prefix + "is_available"] = source_available.to_numpy()
                frame = pd.concat([frame.reset_index(drop=True), extra.reset_index(drop=True)], axis=1)
            frame = _add_spot_perp_basis(frame, primary, request.data_types)
            market = MarketData(symbol, request.timeframe, frame[[
                "open_time", "close_time", "available_time", "open", "high", "low", "close", "volume",
            ] + [column for column in frame.columns if column.startswith(("spot_", "futures_"))]].sort_values("open_time").reset_index(drop=True))
            assets.append(market)
            by_symbol[symbol] = source
        panel = MarketPanel.from_assets(assets)
        expected_slots = pd.date_range(
            pd.Timestamp(plan.warmup_start).ceil(step), plan.label_extension_end,
            freq=step, inclusive="left"
        )
        for symbol in request.symbols:
            actual = panel.frame.xs(symbol, level="symbol").index
            missing = enforce_required_slots(expected_slots, pd.DatetimeIndex(actual), options.missing_data_policy)
            if len(missing):
                missing_slots_by_symbol[symbol] = [stamp.isoformat() for stamp in missing]
        decision_times = panel.frame.available_time
        study_mask = ((decision_times >= pd.Timestamp(request.start))
                      & (decision_times < pd.Timestamp(request.end)))
        worker_mask = ((panel.frame.index.get_level_values("open_time") >= pd.Timestamp(plan.warmup_start))
                       & (decision_times < pd.Timestamp(request.end)))
        if isinstance(universe, DynamicUniverse):
            membership = dynamic_membership_asof(universe, panel)
        elif isinstance(universe, HistoricalUniverse):
            membership = historical_membership_asof(universe, panel)
        else:
            membership = universe.membership(panel)
        if not membership.index.equals(panel.frame.index) or membership.isna().any():
            raise ValueError("universe membership must align to market panel")
        missing_membership_symbols = []
        if type(universe).__name__ == "HistoricalUniverse":
            known = set(universe.listings.symbol)
            missing_membership_symbols = sorted(set(request.symbols) - known)
        if missing_membership_symbols and options.missing_data_policy == "reject":
            raise ValueError("missing point-in-time membership history")
        labels = cross_sectional_labels_v2(
            panel, membership, timeframe=request.timeframe, horizon=request.label_horizon_bars
        )
        label_availability = labels["label_available_time"].where(membership.astype(bool))
        max_availability = label_availability.groupby(level="open_time").transform("max")
        labels["label_available_time"] = max_availability.where(labels.relative_return.notna())
        labels = labels.loc[study_mask].copy()
        labels["label_value"] = labels["relative_return"]
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
        cs_issues = (tuple(missing_source_issues) + tuple(auxiliary_issues)
                     + tuple({"code": "MISSING_PRIMARY_SLOTS", "symbol": symbol, "intervals": intervals}
                             for symbol, intervals in missing_slots_by_symbol.items())
                     + (({"code": "MISSING_MEMBERSHIP_HISTORY", "symbols": missing_membership_symbols},)
                        if missing_membership_symbols else ())
                     + (({"code": "LABEL_EXTENSION_TRUNCATED_AT_SPLIT",
                           "missing_labels": int(labels.label_value.isna().sum())},)
                        if plan.label_extension_truncated else ())
                     + (({"code": "WARMUP_TRUNCATED_AT_SPLIT",
                           "missing_warmup_bars": plan.warmup_truncated_bars},)
                        if plan.warmup_truncated_bars else ()))
        report = DatasetBuildReportV1(sources=source_records, issues=cs_issues, quality={
            "worker_rows": len(worker.frame), "label_rows": len(evaluator.frame),
            "membership_count": int(membership.loc[study_mask].sum()),
        })
        result = DatasetBuildResultV2(worker, evaluator, report, manifest)
        artifact = publish_dataset(result, self.output_root)
        return DatasetBuildResultV2(worker, evaluator, report, manifest, artifact)
