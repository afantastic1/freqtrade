"""Trusted Factory that composes the single-user local Alpha Lab runtime."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat

import pandas as pd

from alpha_lab.data.availability import AvailabilityRuleV1
from alpha_lab.data.dataset_builder import DatasetBuilderV2
from alpha_lab.data.source_resolver import DatasetSourceResolver
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
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

from alpha_lab_local.config import (
    CAMPAIGN_ID, DATASET_KEY, POLICY_SHA256, POLICY_VERSION, RuntimeConfig,
)
from alpha_lab_local.isolation import LocalIsolationVerifierV2


_RULE_PREFIX = "binance-close-plus-30s"


class DiscoveryRequestRegistry:
    """Enforce one BTC futures OHLCV Discovery Campaign at the Agent boundary."""

    def __init__(self, registry, policy_registry):
        self.registry = registry
        self.policy_registry = policy_registry

    def create(self, payload):
        request = ResearchRequestV2.from_dict(dict(payload), self.policy_registry)
        policy = self.policy_registry.get(
            request.campaign_id, request.policy_version, request.campaign_policy_sha256
        )
        discovery = policy.splits["discovery"]
        if (request.campaign_id != CAMPAIGN_ID or request.policy_version != POLICY_VERSION
                or request.campaign_policy_sha256 != POLICY_SHA256
                or request.split != "discovery" or request.symbols != ("BTCUSDT",)
                or request.data_types != ("futures_ohlcv",) or request.mode != "time_series"
                or request.start < discovery.start or request.end > discovery.end):
            raise ValueError("only BTC futures OHLCV time-series Discovery requests are allowed")
        return self.registry.create(payload)

    def get(self, request_id):
        return self.registry.get(request_id)


class DiscoveryCoverageGateway:
    """Keep the public coverage tool inside the single authorized Discovery interval."""

    def __init__(self, gateway, campaign_policy):
        self.gateway = gateway
        self.discovery = campaign_policy.splits["discovery"]

    def get_data_coverage(self, *, dataset_key, start, end):
        try:
            start_utc = parse_rfc3339_utc(start, "start")
            end_utc = parse_rfc3339_utc(end, "end")
        except (TypeError, ValueError):
            return {"status": "INVALID_REQUEST", "error_code": "INVALID_INTERVAL"}
        if (start_utc >= end_utc or start_utc < self.discovery.start
                or end_utc > self.discovery.end):
            return {
                "status": "AUTHORIZATION_DENIED",
                "error_code": "OUTSIDE_DISCOVERY_CAMPAIGN",
            }
        return self.gateway.get_data_coverage(
            dataset_key=dataset_key, start=start, end=end
        )


class TimeframeDatasetBuilder:
    """Select the registered close-delay rule from the request timeframe."""

    def __init__(self, builder):
        self.builder = builder

    def build(self, request, *, options, universe=None):
        rule_id = f"{_RULE_PREFIX}-{request.timeframe}"
        options = replace(options, availability_rule_ids={"futures_ohlcv": rule_id})
        return self.builder.build(request, options=options, universe=universe)


class BtcAvailabilityRuleRegistry:
    """Create the pinned BTC close-delay rule for any policy-authorized timeframe."""

    def get(self, rule_id, *, dataset_identity):
        expected_id = f"{_RULE_PREFIX}-{dataset_identity.timeframe}"
        if (rule_id != expected_id or dataset_identity.exchange != "binance"
                or dataset_identity.market_type != "futures" or dataset_identity.asset != "BTC"
                or dataset_identity.exchange_symbol != "BTC/USDT:USDT"
                or dataset_identity.data_kind != "ohlcv"):
            raise ValueError("availability rule does not match the trusted BTC dataset identity")
        return AvailabilityRuleV1(
            rule_id, dataset_identity, "fixed_delay", "date",
            fixed_delay=pd.Timedelta(seconds=30).to_pytimedelta(),
        )


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
    policy_registry.get(CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256)
    availability = BtcAvailabilityRuleRegistry()
    resolver = DatasetSourceResolver(
        config.catalog_root, config.source_roots, exchange="binance",
        symbol_identities={"BTCUSDT": {
            "exchange": "binance", "market_type": "futures", "asset": "BTC",
            "exchange_symbol": "BTC/USDT:USDT",
        }},
        availability_registry=availability,
    )
    base_builder = DatasetBuilderV2(
        policy_registry, resolver, availability, config.dataset_output_root
    )
    builder = TimeframeDatasetBuilder(base_builder)
    request_registry = DiscoveryRequestRegistry(
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
        config.catalog_root, config.source_roots, config.dataset_registry
    )
    campaign_policy = policy_registry.get(CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256)
    coverage = DiscoveryCoverageGateway(coverage, campaign_policy)
    dataset_options = DatasetBuildOptionsV1(
        "reject", "futures_ohlcv", {}, {"futures_ohlcv": f"{_RULE_PREFIX}-5m"}, {},
    )
    agent_schemas = {
        ("time_series", ("futures_ohlcv",)): ("ohlcv-v1", "1", "json-object-v1", "1")
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
