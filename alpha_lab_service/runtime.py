"""Operator-selected, trusted service dependency loading (never Agent supplied)."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceBundle:
    """Immutable composition of a legacy service and an optional trusted v2 service."""

    v1_service: object
    v2_service: object | None
    agent_gateway: object | None = None


_V2_DEPENDENCIES = {
    "store", "broker", "research_request_registry", "policy_registry",
    "dataset_builder", "dataset_options", "worker_identity", "evaluator_identity",
}
_V2_OPTIONAL_DEPENDENCIES = {"history_stores"}


def build_service_bundle(v1_service, *, v2_dependencies=None, agent_dependencies=None) -> ServiceBundle:
    """Build v2 only from a complete operator-owned dependency set."""
    required_v1 = ("submit_factor", "run_factor", "get_task")
    if any(not callable(getattr(v1_service, name, None)) for name in required_v1):
        raise ValueError("trusted v1 service is incomplete")
    if v2_dependencies is None:
        if agent_dependencies is not None:
            raise ValueError("complete trusted Agent dependencies require Factor Service v2")
        return ServiceBundle(v1_service, None)
    if not isinstance(v2_dependencies, dict):
        raise ValueError("complete trusted v2 dependency set is required")
    unknown = set(v2_dependencies) - (_V2_DEPENDENCIES | _V2_OPTIONAL_DEPENDENCIES | {"universe"})
    if unknown:
        raise ValueError(f"unsupported v2 dependency: {sorted(unknown)[0]}")
    missing = _V2_DEPENDENCIES - set(v2_dependencies)
    if missing or any(v2_dependencies.get(name) is None for name in _V2_DEPENDENCIES):
        raise ValueError("complete trusted v2 dependency set is required")
    deps = dict(v2_dependencies)
    history_stores = deps.get("history_stores")
    if history_stores is None:
        history_stores = {"python-factor-v2": deps["store"]}
        v1_store = getattr(v1_service, "store", None)
        if callable(getattr(v1_store, "list_history", None)):
            history_stores["python-factor-v1"] = v1_store
        legacy_store = getattr(v1_service, "legacy_research_store", None)
        if callable(getattr(legacy_store, "list_runs", None)):
            history_stores["legacy"] = legacy_store
    if not isinstance(history_stores, dict):
        raise ValueError("trusted history stores must be a mapping")
    deps["history_stores"] = history_stores
    worker_identity = deps["worker_identity"]
    if not isinstance(worker_identity, str) or not re.fullmatch(
        r"(?:sha256:[0-9a-f]{64}|.+@sha256:[0-9a-f]{64})", worker_identity
    ):
        raise ValueError("trusted Worker identity must be an immutable image digest")
    if not callable(getattr(deps["broker"], "run_chunk", None)):
        raise ValueError("trusted v2 Broker must expose run_chunk")
    if not callable(getattr(deps["store"], "create_or_get_task", None)):
        raise ValueError("trusted v2 Store is unavailable")
    if not callable(getattr(deps["dataset_builder"], "build", None)):
        raise ValueError("trusted DatasetBuilder is unavailable")
    if not callable(getattr(deps["research_request_registry"], "get", None)):
        raise ValueError("trusted ResearchRequest registry is unavailable")
    if not callable(getattr(deps["policy_registry"], "get", None)):
        raise ValueError("trusted Campaign policy registry is unavailable")
    from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1

    if not isinstance(deps["dataset_options"], DatasetBuildOptionsV1):
        raise ValueError("server-owned DatasetBuilder options are required")
    evaluator_identity = deps["evaluator_identity"]
    if not isinstance(evaluator_identity, str) or not evaluator_identity or len(evaluator_identity) > 128:
        raise ValueError("trusted evaluator identity is required")
    from alpha_lab_service.jobs_v2 import ResearchServiceV2

    v2_service = ResearchServiceV2(**deps)
    if agent_dependencies is None:
        return ServiceBundle(v1_service, v2_service)
    if not isinstance(agent_dependencies, dict):
        raise ValueError("complete trusted Agent dependencies are required")
    agent_allowed = {"coverage_gateway", "input_schema_registry", "isolation_verifier"}
    unknown_agent = set(agent_dependencies) - agent_allowed
    if unknown_agent:
        raise ValueError(f"unsupported trusted Agent dependency: {sorted(unknown_agent)[0]}")
    missing_agent = {"coverage_gateway", "input_schema_registry", "isolation_verifier"} - set(agent_dependencies)
    if missing_agent:
        raise ValueError("complete trusted Agent dependencies are required")
    from alpha_lab_service.agent_gateway_v2 import AgentResearchGatewayV2

    agent_gateway = AgentResearchGatewayV2(
        request_registry=deps["research_request_registry"],
        coverage_gateway=agent_dependencies["coverage_gateway"],
        factor_service=v2_service,
        isolation_verifier=agent_dependencies["isolation_verifier"],
        input_schema_registry=agent_dependencies["input_schema_registry"],
    )
    return ServiceBundle(v1_service, v2_service, agent_gateway)


def load_service_factory(specification: str):
    if not specification or ":" not in specification:
        raise ValueError("factory must be configured as trusted_module:callable")
    module_name, attribute = specification.split(":", 1)
    if not module_name.replace("_", "").replace(".", "").isalnum() or not attribute.isidentifier():
        raise ValueError("invalid trusted factory reference")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError("configured service factory is not callable")
    return factory
