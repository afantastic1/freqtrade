import asyncio

import pytest

from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.authorization_v2 import MappingResearchRequestRegistryV1
from alpha_lab_service.runtime import build_service_bundle
from alpha_lab_service.server import create_mcp_server
from alpha_lab_service.store_v2 import ResearchStoreV2
from alpha_lab_service.tests.test_v2_authorization import FakeDatasetBuilder, research_request


TOKEN = "test-token-0123456789-abcdefghijkl"


class V1Service:
    def submit_factor(self, request):
        return {"status": "SUBMITTED", "request": request}

    async def run_factor(self, task_id):
        return {"status": "COMPLETED", "task_id": task_id}

    def get_task(self, task_id):
        return {"status": "QUEUED", "task_id": task_id}

    def get_history(self, limit=100):
        return []

    def append_decision(self, **kwargs):
        return kwargs


class V2Service:
    def submit_factor_v2(self, request):
        return {"task_id": "v2-task", "status": "QUEUED"}

    async def run_factor_v2(self, task_id):
        return {"task_id": task_id, "status": "DIAGNOSTIC"}

    def get_task_v2(self, task_id):
        return {"task_id": task_id, "status": "RUNNING"}


def test_mcp_adds_exact_v2_tool_schemas_and_preserves_v1_routes():
    v1_only, _ = create_mcp_server(V1Service(), bearer_token=TOKEN)
    names_before = {tool.name for tool in asyncio.run(v1_only.list_tools())}
    assert names_before == {"submit_factor", "run_factor", "get_task", "get_history", "append_decision"}

    server, _app = create_mcp_server(V1Service(), v2_service=V2Service(), bearer_token=TOKEN)
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == names_before | {"submit_factor_v2", "run_factor_v2", "get_task_v2"}
    submit_schema = by_name["submit_factor_v2"].inputSchema
    assert set(submit_schema["properties"]) == {"request"}
    assert submit_schema["required"] == ["request"]
    for name in ("run_factor_v2", "get_task_v2"):
        schema = by_name[name].inputSchema
        assert set(schema["properties"]) == {"task_id"}
        assert schema["required"] == ["task_id"]


def test_service_bundle_rejects_partial_v2_dependencies_and_caller_control_fields():
    v1 = V1Service()
    assert build_service_bundle(v1).v2_service is None
    with pytest.raises(ValueError, match="complete trusted v2 dependency set"):
        build_service_bundle(v1, v2_dependencies={"broker": object()})
    with pytest.raises(ValueError, match="unsupported v2 dependency"):
        build_service_bundle(v1, v2_dependencies={
            "store": object(), "broker": object(), "research_request_registry": object(),
            "policy_registry": object(), "dataset_builder": object(), "dataset_options": object(),
            "worker_identity": "sha256:" + "a" * 64, "evaluator_identity": "evaluator-v1",
            "limits": "caller-controlled",
        })


def test_trusted_bundle_wires_complete_v2_service(tmp_path):
    class Broker:
        async def run_chunk(self, *_args):
            return []

    bundle = build_service_bundle(V1Service(), v2_dependencies={
        "store": ResearchStoreV2(tmp_path / "v2" / "store.sqlite3", protected_paths=()),
        "broker": Broker(),
        "research_request_registry": MappingResearchRequestRegistryV1({
            "example-request-001": research_request(),
        }),
        "policy_registry": FileCampaignPolicyRegistry(),
        "dataset_builder": FakeDatasetBuilder(),
        "dataset_options": DatasetBuildOptionsV1("preserve", "futures_ohlcv", {}, {}, {}),
        "worker_identity": "sha256:" + "a" * 64,
        "evaluator_identity": "evaluator-v1",
    })
    assert bundle.v2_service is not None
    assert {tool.name for tool in asyncio.run(create_mcp_server(
        bundle, bearer_token=TOKEN
    )[0].list_tools())} >= {"submit_factor_v2", "run_factor_v2", "get_task_v2"}


def test_mcp_does_not_accept_trusted_runtime_selection_fields():
    server, _ = create_mcp_server(V1Service(), v2_service=V2Service(), bearer_token=TOKEN)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in ("submit_factor_v2", "run_factor_v2", "get_task_v2"):
        schema = tools[name].inputSchema
        assert "store" not in schema["properties"]
        assert "worker_identity" not in schema["properties"]
        assert "limits" not in schema["properties"]
        assert "factory" not in schema["properties"]
