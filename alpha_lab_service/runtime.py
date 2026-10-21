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


_V2_DEPENDENCIES = {
    "store", "broker", "research_request_registry", "policy_registry",
    "dataset_builder", "dataset_options", "worker_identity", "evaluator_identity",
}


def build_service_bundle(v1_service, *, v2_dependencies=None) -> ServiceBundle:
    """Build v2 only from a complete operator-owned dependency set."""
    required_v1 = ("submit_factor", "run_factor", "get_task")
    if any(not callable(getattr(v1_service, name, None)) for name in required_v1):
        raise ValueError("trusted v1 service is incomplete")
    if v2_dependencies is None:
        return ServiceBundle(v1_service, None)
    if not isinstance(v2_dependencies, dict):
        raise ValueError("complete trusted v2 dependency set is required")
    unknown = set(v2_dependencies) - (_V2_DEPENDENCIES | {"universe"})
    if unknown:
        raise ValueError(f"unsupported v2 dependency: {sorted(unknown)[0]}")
    missing = _V2_DEPENDENCIES - set(v2_dependencies)
    if missing or any(v2_dependencies.get(name) is None for name in _V2_DEPENDENCIES):
        raise ValueError("complete trusted v2 dependency set is required")
    deps = dict(v2_dependencies)
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

    return ServiceBundle(v1_service, ResearchServiceV2(**deps))


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
