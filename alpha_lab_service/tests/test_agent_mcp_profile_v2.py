from __future__ import annotations

import asyncio

from alpha_lab_service.auth import BearerAuthMiddleware
from alpha_lab_service.server import create_agent_mcp_server


TOKEN = "agent-profile-test-token-0123456789"
EXPECTED_TOOLS = {
    "get_data_coverage",
    "create_research_request_v2",
    "submit_factor_v2",
    "run_factor_v2",
    "get_task_status",
    "get_task_report",
    "get_history_v2",
}


class FakeGateway:
    def get_data_coverage(self, **_kwargs):
        return {"status": "AVAILABLE"}

    def create_research_request_v2(self, _request):
        return {"status": "AUTHORIZED"}

    def submit_factor_v2(self, **_kwargs):
        return {"status": "QUEUED", "task_id": "task-1"}

    async def run_factor_v2(self, task_id):
        return {"status": "EXECUTION_BLOCKED", "task_id": task_id}

    def get_task_status(self, task_id):
        return {"status": "QUEUED", "task_id": task_id}

    def get_task_report(self, task_id):
        return {"status": "QUEUED", "task_id": task_id}

    def get_history_v2(self, limit=100):
        return {"entries": [], "source_status": {}, "source_errors": {}}


def test_agent_profile_exposes_exactly_the_restricted_tools_and_arguments():
    server, _app = create_agent_mcp_server(FakeGateway(), bearer_token=TOKEN)

    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == EXPECTED_TOOLS
    expected_properties = {
        "get_data_coverage": {"dataset_key", "start", "end"},
        "create_research_request_v2": {"request"},
        "submit_factor_v2": {"research_request_id", "source", "params", "execution_mode"},
        "run_factor_v2": {"task_id"},
        "get_task_status": {"task_id"},
        "get_task_report": {"task_id"},
        "get_history_v2": {"limit"},
    }
    for name, expected in expected_properties.items():
        schema = by_name[name].inputSchema
        assert set(schema["properties"]) == expected
        assert not expected.intersection({
            "path", "source_path", "catalog_root", "policy", "policy_sha256",
            "worker_identity", "isolation_proof", "store_path",
        })


def test_agent_profile_uses_bearer_auth_middleware():
    _server, app = create_agent_mcp_server(FakeGateway(), bearer_token=TOKEN)
    assert isinstance(app, BearerAuthMiddleware)

    async def request_without_token():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            sent.append(event)

        await app({"type": "http", "path": "/mcp", "headers": []}, receive, send)
        return sent

    response = asyncio.run(request_without_token())
    assert response[0]["status"] == 401


def test_agent_profile_rejects_short_bearer_tokens():
    try:
        create_agent_mcp_server(FakeGateway(), bearer_token="short")
    except ValueError as exc:
        assert "24 characters" in str(exc)
    else:
        raise AssertionError("short bearer token was accepted")
