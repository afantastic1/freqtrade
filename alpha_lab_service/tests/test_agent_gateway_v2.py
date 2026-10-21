from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.agent_gateway_v2 import AgentResearchGatewayV2
from alpha_lab_service.isolation_v2 import (
    REQUIRED_ISOLATION_CHECKS_V2,
    IsolationReadinessV2,
)
from alpha_lab_service.request_registry_v2 import ResearchRequestRegistryV2
from alpha_lab_service.runtime import build_service_bundle
from alpha_lab_service.store_v2 import ResearchStoreV2


WORKER_IMAGE = "sha256:" + "a" * 64
AUTHORIZED_REQUEST = {
    "protocol_version": "2.0-research-request",
    "request_id": "agent-request-001",
    "campaign_id": "dataset_builder_acceptance",
    "policy_version": "1",
    "campaign_policy_sha256": "b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f",
    "start": "2026-02-01T00:00:00Z",
    "end": "2026-02-02T00:00:00Z",
    "symbols": ["BTCUSDT"],
    "timeframe": "5m",
    "mode": "time_series",
    "data_types": ["futures_ohlcv"],
    "split": "holdout",
    "lookback_bars": 20,
    "label_horizon_bars": 3,
    "embargo_bars": 1,
}
SCHEMAS = {
    ("time_series", ("futures_ohlcv",)): (
        "ohlcv-v1", "1", "json-object-v1", "1"
    ),
}


class CoverageGateway:
    def get_data_coverage(self, **kwargs):
        return {"status": "AVAILABLE", "dataset_id": "trusted-dataset", **kwargs}


class FakeIsolationVerifier:
    def __init__(self, readiness):
        self.readiness = readiness
        self.images = []

    def verify(self, *, worker_image_digest):
        self.images.append(worker_image_digest)
        return self.readiness


class FakeStore:
    def __init__(self):
        self.task = {
            "task_id": "task-123", "request_hash": "f" * 64,
            "plan_hash": "e" * 64, "chunk_count": 1, "created_at": "2026-02-01T00:00:00Z",
            "status": "QUEUED",
        }
        self.evidence = None
        self.evidence_calls = 0

    def get_task(self, _task_id):
        return dict(self.task)

    def get_evidence(self, _run_id):
        self.evidence_calls += 1
        return self.evidence


class FakeFactorService:
    worker_identity = WORKER_IMAGE
    policy_registry = FileCampaignPolicyRegistry()

    def __init__(self):
        self.store = FakeStore()
        self.submitted = []
        self.run_calls = []
        self.history = {
            "entries": [{
                "kind": "EVIDENCE", "identity": "f" * 64, "store_id": "test-store",
                "protocol_version": "2", "payload": {
                    "status": "EXPLORATORY", "report": {"ic": 0.1},
                    "source": "secret source text", "local_path": "/host/private/data.feather",
                },
                "host_path": "/host/private/store.sqlite3",
            }],
            "source_status": {"python-factor-v2": "AVAILABLE"},
            "source_errors": {},
        }

    def submit_factor_v2(self, payload):
        self.submitted.append(payload)
        return {"task_id": "task-123", "request_hash": "f" * 64, "status": "QUEUED"}

    async def run_factor_v2(self, task_id):
        self.run_calls.append(task_id)
        return {"task_id": task_id, "status": "EXPLORATORY"}

    def get_task_v2(self, task_id):
        if task_id != self.store.task["task_id"]:
            return None
        return dict(self.store.task)

    def get_history_v2(self, _limit=100):
        return self.history


def _readiness(*, image=WORKER_IMAGE, verified_at=None, checks=None):
    return IsolationReadinessV2(
        worker_image_digest=image,
        verified_at=verified_at or datetime.now(timezone.utc),
        checks=checks if checks is not None else {name: True for name in REQUIRED_ISOLATION_CHECKS_V2},
    )


def _gateway(tmp_path, *, verifier=None, schema_registry=SCHEMAS, service=None):
    policy_registry = FileCampaignPolicyRegistry()
    request_registry = ResearchRequestRegistryV2(
        tmp_path / "requests.sqlite3", policy_registry
    )
    request_registry.create(AUTHORIZED_REQUEST)
    factor_service = service or FakeFactorService()
    gateway = AgentResearchGatewayV2(
        request_registry=request_registry,
        coverage_gateway=CoverageGateway(),
        factor_service=factor_service,
        isolation_verifier=verifier,
        input_schema_registry=schema_registry,
    )
    return gateway, factor_service


def test_submit_binds_source_to_authorized_request_and_trusted_schema(tmp_path):
    gateway, factor_service = _gateway(tmp_path)

    result = gateway.submit_factor_v2(
        research_request_id="agent-request-001",
        source="def alpha(data, store): return 1.0",
        params={"lookback": 8},
        execution_mode="stateless",
    )

    assert result == {"task_id": "task-123", "request_hash": "f" * 64, "status": "QUEUED"}
    assert factor_service.submitted == [{
        "research_request_id": "agent-request-001",
        "source": "def alpha(data, store): return 1.0",
        "params": {"lookback": 8},
        "worker_input_schema_id": "ohlcv-v1",
        "worker_input_schema_version": "1",
        "execution_mode": "stateless",
        "state_schema_id": None,
        "state_schema_version": None,
    }]


def test_create_research_request_returns_authorized_canonical_summary(tmp_path):
    gateway, _ = _gateway(tmp_path)

    result = gateway.create_research_request_v2(AUTHORIZED_REQUEST)

    assert result["status"] == "AUTHORIZED"
    assert result["request_id"] == "agent-request-001"
    assert result["campaign_id"] == "dataset_builder_acceptance"
    assert result["symbols"] == ["BTCUSDT"]
    assert result["split"] == "holdout"
    assert "path" not in result


def test_submit_rejects_caller_attempt_to_widen_scope_or_choose_schema(tmp_path):
    gateway, _ = _gateway(tmp_path)

    with pytest.raises(TypeError):
        gateway.submit_factor_v2(
            research_request_id="agent-request-001", source="x", params={},
            execution_mode="stateless", symbols=["DOGEUSDT"],
        )
    with pytest.raises(TypeError):
        gateway.submit_factor_v2(
            research_request_id="agent-request-001", source="x", params={},
            execution_mode="stateless", worker_input_schema_id="attacker-schema",
        )


def test_submit_uses_trusted_state_schema_for_stateful_execution(tmp_path):
    gateway, factor_service = _gateway(tmp_path)

    gateway.submit_factor_v2(
        research_request_id="agent-request-001", source="x", params={},
        execution_mode="stateful",
    )

    assert factor_service.submitted[0]["state_schema_id"] == "json-object-v1"
    assert factor_service.submitted[0]["state_schema_version"] == "1"


def test_submit_rejects_unmapped_authorized_request_schema(tmp_path):
    gateway, factor_service = _gateway(tmp_path, schema_registry={})

    with pytest.raises(ValueError, match="no trusted Worker schema"):
        gateway.submit_factor_v2(
            research_request_id="agent-request-001", source="x", params={},
            execution_mode="stateless",
        )
    assert factor_service.submitted == []


def test_submit_rejects_source_over_the_v2_byte_limit(tmp_path):
    gateway, factor_service = _gateway(tmp_path)

    with pytest.raises(ValueError, match="source exceeds byte limit"):
        gateway.submit_factor_v2(
            research_request_id="agent-request-001", source="x" * (256 * 1024 + 1),
            params={}, execution_mode="stateless",
        )
    assert factor_service.submitted == []


@pytest.mark.parametrize(
    "readiness",
    [
        None,
        _readiness(checks={"network_disabled": True}),
        _readiness(image="sha256:" + "b" * 64),
        _readiness(verified_at=datetime.now(timezone.utc) - timedelta(minutes=6)),
    ],
)
def test_run_blocks_without_complete_fresh_image_pinned_isolation(tmp_path, readiness):
    verifier = FakeIsolationVerifier(readiness) if readiness is not None else None
    gateway, factor_service = _gateway(tmp_path, verifier=verifier)

    result = asyncio.run(gateway.run_factor_v2("task-123"))

    assert result == {
        "task_id": "task-123", "status": "EXECUTION_BLOCKED",
        "error_code": "ISOLATION_NOT_VERIFIED",
    }
    assert factor_service.run_calls == []
    assert factor_service.store.evidence_calls == 0


def test_run_dispatches_only_after_trusted_isolation_verifies_pinned_image(tmp_path):
    verifier = FakeIsolationVerifier(_readiness())
    gateway, factor_service = _gateway(tmp_path, verifier=verifier)

    result = asyncio.run(gateway.run_factor_v2("task-123"))

    assert result == {"task_id": "task-123", "status": "EXPLORATORY"}
    assert verifier.images == [WORKER_IMAGE]
    assert factor_service.run_calls == ["task-123"]


def test_run_does_not_probe_isolation_or_dispatch_unknown_task(tmp_path):
    verifier = FakeIsolationVerifier(_readiness())
    gateway, factor_service = _gateway(tmp_path, verifier=verifier)

    result = asyncio.run(gateway.run_factor_v2("unknown-task"))

    assert result == {"status": "NOT_FOUND", "error_code": "TASK_NOT_FOUND"}
    assert verifier.images == []
    assert factor_service.run_calls == []


def test_task_status_and_report_never_return_source_or_host_paths(tmp_path):
    gateway, factor_service = _gateway(tmp_path)
    factor_service.store.task["status"] = "COMPLETED"
    factor_service.store.evidence = {
        "run_id": "f" * 64, "task_id": "task-123", "evidence_hash": "d" * 64,
        "evidence": {
            "status": "EXPLORATORY", "report": {
                "ic": 0.1, "path": "/host/data.feather", "description": "loaded /home/private/data.feather"
            },
            "source": "private source", "formal_inference_status": "BLOCKED",
        },
    }

    status = gateway.get_task_status("task-123")
    report = gateway.get_task_report("task-123")

    assert status == {
        "task_id": "task-123", "status": "COMPLETED", "request_hash": "f" * 64,
        "plan_hash": "e" * 64, "chunk_count": 1, "created_at": "2026-02-01T00:00:00Z",
    }
    assert report == {
        "task_id": "task-123", "run_id": "f" * 64, "status": "EXPLORATORY",
        "evidence_hash": "d" * 64, "report": {"ic": 0.1, "description": None},
        "formal_inference_status": "BLOCKED",
    }
    assert "private source" not in str(report)
    assert "/host/" not in str(report)


def test_report_is_unavailable_until_complete_evidence_exists(tmp_path):
    gateway, _ = _gateway(tmp_path)

    result = gateway.get_task_report("task-123")

    assert result == {"task_id": "task-123", "status": "QUEUED"}


def test_history_is_bounded_and_strips_source_and_host_path_fields(tmp_path):
    gateway, _ = _gateway(tmp_path)

    result = gateway.get_history_v2(limit=10)

    assert result["entries"][0]["payload"] == {
        "status": "EXPLORATORY", "report": {"ic": 0.1}
    }
    assert "/host/" not in str(result)
    assert "secret source text" not in str(result)
    with pytest.raises(ValueError, match="history limit"):
        gateway.get_history_v2(limit=101)


def test_runtime_constructs_agent_gateway_only_from_complete_trusted_dependencies(tmp_path):
    class V1Service:
        def submit_factor(self, _request): ...
        async def run_factor(self, _task_id): ...
        def get_task(self, _task_id): ...

    class Broker:
        async def run_chunk(self, *_args):
            return []

    class DatasetBuilder:
        def build(self, *_args, **_kwargs):
            return None

    v2_dependencies = {
        "store": ResearchStoreV2(tmp_path / "store.sqlite3", protected_paths=()),
        "broker": Broker(),
        "research_request_registry": ResearchRequestRegistryV2(
            tmp_path / "trusted-requests.sqlite3", FileCampaignPolicyRegistry()
        ),
        "policy_registry": FileCampaignPolicyRegistry(),
        "dataset_builder": DatasetBuilder(),
        "dataset_options": DatasetBuildOptionsV1("preserve", "futures_ohlcv", {}, {}, {}),
        "worker_identity": WORKER_IMAGE,
        "evaluator_identity": "evaluator-v1",
    }

    without_agent = build_service_bundle(V1Service(), v2_dependencies=v2_dependencies)
    assert without_agent.agent_gateway is None
    with pytest.raises(ValueError, match="complete trusted Agent dependencies"):
        build_service_bundle(
            V1Service(), v2_dependencies=v2_dependencies, agent_dependencies={"coverage_gateway": CoverageGateway()}
        )
    with_agent = build_service_bundle(
        V1Service(), v2_dependencies=v2_dependencies,
        agent_dependencies={
            "coverage_gateway": CoverageGateway(),
            "input_schema_registry": SCHEMAS,
            "isolation_verifier": None,
        },
    )
    assert isinstance(with_agent.agent_gateway, AgentResearchGatewayV2)
