"""Trusted Factory that composes the single-user local Alpha Lab runtime."""

from __future__ import annotations

from dataclasses import replace
import json
from itertools import combinations
import os
from pathlib import Path
import stat

import pandas as pd

from alpha_lab.data.availability import AvailabilityRuleV1
from alpha_lab.data.dataset_builder import DatasetBuilderV2
from alpha_lab.data.source_resolver import DatasetSourceResolver
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab.data.universe import FixedUniverse
from alpha_lab.data.timeframe import timeframe_to_seconds
from data_engineering.sources.contracts import DatasetIdentity
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_contracts._timestamps import parse_rfc3339_utc
from alpha_lab_service.broker_client_v2 import BrokerClientV2
from alpha_lab_service.catalog_gateway_v2 import CatalogCoverageGatewayV2
from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY
from alpha_lab_service.request_registry_v2 import ResearchRequestRegistryV2
from alpha_lab_service.runtime import ServiceBundle
from alpha_lab_service.store_v2 import ResearchStoreV2
from alpha_lab_service.worker_profile import profile_from_file
from alpha_lab_service.jobs_v2 import ResearchServiceV2
from alpha_lab_service.agent_gateway_v2 import AgentResearchGatewayV2

from alpha_lab_local.config import CAMPAIGN_ID, POLICY_SHA256, POLICY_VERSION, RuntimeConfig
from alpha_lab_local.isolation import LocalIsolationVerifierV2
from data_engineering.catalog_v3 import list_datasets


_RULE_PREFIX = "binance-close-plus-30s"


class CampaignSplitRequestRegistry:
    """Enforce the trusted campaign split windows at the Agent boundary."""

    def __init__(self, registry, policy_registry):
        self.registry = registry
        self.policy_registry = policy_registry

    def create(self, payload):
        request = ResearchRequestV2.from_dict(dict(payload), self.policy_registry)
        policy = self.policy_registry.get(
            request.campaign_id, request.policy_version, request.campaign_policy_sha256
        )
        if (request.campaign_id != CAMPAIGN_ID or request.policy_version != POLICY_VERSION
                or request.campaign_policy_sha256 != POLICY_SHA256
                or request.start < policy.splits[request.split].start
                or request.end > policy.splits[request.split].end):
            raise ValueError("request is outside the trusted campaign and selected split")
        if not set(request.data_types).intersection({"futures_ohlcv", "spot_ohlcv"}):
            raise ValueError("research requests must include Spot or Perp OHLCV as the label market")
        return self.registry.create(payload)

    def get(self, request_id):
        return self.registry.get(request_id)


class CampaignSplitCoverageGateway:
    """Keep public coverage requests inside one trusted campaign split."""

    def __init__(self, gateway, campaign_policy):
        self.gateway = gateway
        self.splits = campaign_policy.splits

    def get_data_coverage(self, *, dataset_key, start, end):
        try:
            start_utc = parse_rfc3339_utc(start, "start")
            end_utc = parse_rfc3339_utc(end, "end")
        except (TypeError, ValueError):
            return {"status": "INVALID_REQUEST", "error_code": "INVALID_INTERVAL"}
        within_one_split = any(
            start_utc >= split.start and end_utc <= split.end
            for split in self.splits.values()
        )
        if start_utc >= end_utc or not within_one_split:
            return {
                "status": "AUTHORIZATION_DENIED",
                "error_code": "OUTSIDE_AUTHORIZED_CAMPAIGN_SPLIT",
            }
        return self.gateway.get_data_coverage(
            dataset_key=dataset_key, start=start, end=end
        )


class RequestDatasetBuilder:
    """Bind one trusted PIT rule to every explicitly requested source."""

    def __init__(self, builder):
        self.builder = builder

    def build(self, request, *, options, universe=None):
        ohlcv = [name for name in request.data_types if name in {"futures_ohlcv", "spot_ohlcv"}]
        primary = options.primary_data_type
        if primary not in ohlcv:
            primary = "futures_ohlcv" if "futures_ohlcv" in ohlcv else (ohlcv[0] if ohlcv else None)
        rule_ids = {f"{symbol}:{data_type}": _RULE_PREFIX
                    for symbol in request.symbols for data_type in request.data_types}
        seconds = timeframe_to_seconds(request.timeframe)
        max_ages = dict(options.max_age_by_data_type)
        if "funding_rate" in request.data_types:
            max_ages.setdefault("funding_rate", 9 * 60 * 60)
        if "open_interest" in request.data_types:
            max_ages.setdefault("open_interest", 2 * seconds)
        options = replace(options, primary_data_type=primary,
                          availability_rule_ids=rule_ids, max_age_by_data_type=max_ages)
        if request.mode == "cross_sectional" and universe is None:
            universe = FixedUniverse(f"request:{request.request_id}", request.symbols)
        return self.builder.build(request, options=options, universe=universe)


class BinanceAvailabilityRuleRegistry:
    """Create a versioned availability rule for registered Binance data."""

    def get(self, rule_id, *, dataset_identity):
        if (rule_id != _RULE_PREFIX or dataset_identity.exchange != "binance"
                or dataset_identity.data_kind not in {"ohlcv", "funding_rate", "open_interest", "mark_price"}):
            raise ValueError("availability rule does not match a registered Binance data identity")
        timeframe = (pd.Timedelta(seconds=timeframe_to_seconds(dataset_identity.timeframe))
                     if dataset_identity.data_kind in {"ohlcv", "mark_price"}
                     else None)
        return AvailabilityRuleV1(
            rule_id, dataset_identity, "fixed_delay", "date",
            fixed_delay=pd.Timedelta(seconds=30).to_pytimedelta(),
            max_age=(timeframe * 2 if timeframe is not None else None),
        )


def _catalog_dataset_registry(config):
    """Build coverage aliases from validated Catalog identities, not a BTC list."""
    registry = dict(config.dataset_registry)
    for record in list_datasets(config.catalog_root):
        manifest = record.get("manifest", {})
        if manifest.get("exchange") != "binance":
            continue
        key = "binance:{market}:{asset}:{kind}:{timeframe}".format(
            market=manifest["market_type"], asset=manifest["asset"],
            kind=manifest["data_kind"], timeframe=manifest.get("timeframe") or "event",
        )
        dataset_id = manifest["dataset_id"]
        prior = registry.get(key)
        if prior is not None and prior != dataset_id:
            raise ValueError("Catalog identity maps to conflicting dataset IDs")
        registry[key] = dataset_id
    return registry


def _load_config() -> RuntimeConfig:
    path = os.environ.get("ALPHA_LAB_LOCAL_CONFIG", "")
    if not path:
        raise ValueError("trusted local Factory config is not set")
    return RuntimeConfig.from_file(path)


def _check_private_state(config: RuntimeConfig) -> None:
    state_root = config.research_store_path.parent
    if state_root.is_symlink() or not state_root.is_dir():
        raise ValueError("private Alpha Lab state directory is unavailable")
    info = state_root.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("private Alpha Lab state directory permissions are too broad")
    config.dataset_output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config.dataset_output_root, 0o700)


def create():
    """Build all dependencies from the read-only in-image config and private env."""
    config = _load_config()
    _check_private_state(config)
    broker_token = os.environ.get("ALPHA_LAB_BROKER_TOKEN", "")
    if len(broker_token) < 24:
        raise ValueError("private Broker credential is unavailable")

    profile = profile_from_file(config.worker_profile_path)
    policy_registry = FileCampaignPolicyRegistry()
    campaign_policy = policy_registry.get(CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256)
    availability = BinanceAvailabilityRuleRegistry()
    symbol_identities = {}
    for symbol in campaign_policy.symbols:
        asset = symbol.removesuffix("USDT")
        symbol_identities[symbol] = {
            "exchange": "binance", "market_type": "futures", "asset": asset,
            "exchange_symbol": f"{asset}/USDT:USDT",
            "spot_ohlcv": {
                "exchange": "binance", "market_type": "spot", "asset": asset,
                "exchange_symbol": f"{asset}/USDT",
            },
        }
    resolver = DatasetSourceResolver(
        config.catalog_root, config.source_roots, exchange="binance",
        symbol_identities=symbol_identities,
        availability_registry=availability,
    )
    base_builder = DatasetBuilderV2(
        policy_registry, resolver, availability, config.dataset_output_root
    )
    builder = RequestDatasetBuilder(base_builder)
    request_registry = CampaignSplitRequestRegistry(
        ResearchRequestRegistryV2(config.research_request_path, policy_registry),
        policy_registry,
    )
    try:
        os.chmod(config.research_request_path, 0o600)
    except FileNotFoundError:
        pass
    store = ResearchStoreV2(config.research_store_path)
    broker = BrokerClientV2(config.broker_endpoint, broker_token)
    coverage = CatalogCoverageGatewayV2(
        config.catalog_root, config.source_roots, _catalog_dataset_registry(config)
    )
    coverage = CampaignSplitCoverageGateway(coverage, campaign_policy)
    dataset_options = DatasetBuildOptionsV1(
        "reject", "futures_ohlcv", {}, {}, {},
    )
    data_types = tuple(sorted(campaign_policy.data_types))
    agent_schemas = {
        (mode, subset): ("ohlcv-v1", "1", "json-object-v1", "1")
        for mode in campaign_policy.modes
        for size in range(1, len(data_types) + 1)
        for subset in combinations(data_types, size)
    }
    # Ensure the package registry has the exact trusted schema binding used above.
    INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1")
    v2_service = ResearchServiceV2(
        store, broker, research_request_registry=request_registry,
        policy_registry=policy_registry, dataset_builder=builder,
        dataset_options=dataset_options, worker_identity=profile.image,
        evaluator_identity="alpha-lab-descriptive-v2",
    )
    agent_gateway = AgentResearchGatewayV2(
        request_registry=request_registry, coverage_gateway=coverage,
        factor_service=v2_service,
        isolation_verifier=LocalIsolationVerifierV2(config, profile),
        input_schema_registry=agent_schemas,
    )
    return ServiceBundle(v1_service=None, v2_service=v2_service, agent_gateway=agent_gateway)
