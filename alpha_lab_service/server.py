"""MCP Streamable HTTP control plane. Submitted factor code is never imported here."""

from __future__ import annotations

import argparse
import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

from alpha_lab_service.auth import BearerAuthMiddleware
from alpha_lab_service.runtime import load_service_factory


def _agent_error(exc: Exception) -> dict:
    message = str(exc).lower()
    if isinstance(exc, KeyError) or "unknown research request" in message or "unknown task" in message:
        return {"status": "NOT_FOUND", "error_code": "UNKNOWN_IDENTIFIER"}
    if any(token in message for token in ("campaign", "policy", "authorization", "authorized", "outside the selected")):
        return {"status": "AUTHORIZATION_DENIED", "error_code": "CAMPAIGN_AUTHORIZATION_DENIED"}
    if isinstance(exc, (TypeError, ValueError)):
        return {"status": "INVALID_REQUEST", "error_code": "INVALID_REQUEST"}
    return {"status": "UNAVAILABLE", "error_code": "SERVICE_UNAVAILABLE"}


def _agent_call(operation):
    try:
        return operation()
    except Exception as exc:
        return _agent_error(exc)


def create_agent_mcp_server(gateway, *, bearer_token: str, host: str = "127.0.0.1", port: int = 8000):
    """Create the restricted Agent tool profile without legacy mutation tools."""
    methods = (
        "get_data_coverage", "create_research_request_v2", "submit_factor_v2",
        "run_factor_v2", "get_task_status", "get_task_report", "get_history_v2",
    )
    if any(not callable(getattr(gateway, name, None)) for name in methods):
        raise ValueError("trusted Agent research gateway is incomplete")
    mcp = FastMCP(
        "Alpha Lab Authorized Agent Research",
        instructions="Use authorized Catalog coverage and Campaign-bound research requests. Execution is isolated and may be blocked.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )

    @mcp.tool()
    def get_data_coverage(dataset_key: str, start: str, end: str) -> dict:
        """Read coverage for one operator-configured dataset alias and UTC interval."""
        return _agent_call(lambda: gateway.get_data_coverage(
            dataset_key=dataset_key, start=start, end=end
        ))

    @mcp.tool()
    def create_research_request_v2(request: dict) -> dict:
        """Create an append-only ResearchRequest after trusted Campaign validation."""
        return _agent_call(lambda: gateway.create_research_request_v2(request))

    @mcp.tool()
    def submit_factor_v2(
        research_request_id: str,
        source: str,
        params: dict,
        execution_mode: Literal["stateless", "stateful"],
    ) -> dict:
        """Submit bounded factor source content bound to one authorized request."""
        return _agent_call(lambda: gateway.submit_factor_v2(
            research_request_id=research_request_id,
            source=source,
            params=params,
            execution_mode=execution_mode,
        ))

    @mcp.tool()
    async def run_factor_v2(task_id: str) -> dict:
        """Run a task only after the trusted isolation gate passes."""
        try:
            return await gateway.run_factor_v2(task_id)
        except Exception as exc:
            return _agent_error(exc)

    @mcp.tool()
    def get_task_status(task_id: str) -> dict:
        """Read a sanitized task state."""
        return _agent_call(lambda: gateway.get_task_status(task_id))

    @mcp.tool()
    def get_task_report(task_id: str) -> dict:
        """Read the descriptive report from complete Evidence only."""
        return _agent_call(lambda: gateway.get_task_report(task_id))

    @mcp.tool()
    def get_history_v2(limit: int = 100) -> dict:
        """Read bounded unified research history."""
        return _agent_call(lambda: gateway.get_history_v2(limit))

    app = BearerAuthMiddleware(mcp.streamable_http_app(), bearer_token)
    return mcp, app


def create_mcp_server(service, *, v2_service=None, bearer_token: str, host: str = "127.0.0.1", port: int = 8000):
    if hasattr(service, "v1_service") and hasattr(service, "v2_service"):
        v2_service = service.v2_service
        service = service.v1_service
    elif v2_service is None:
        v2_service = getattr(service, "v2_service", None)
    if v2_service is not None and any(not callable(getattr(v2_service, name, None)) for name in (
        "submit_factor_v2", "run_factor_v2", "get_task_v2", "get_history_v2",
    )):
        raise ValueError("trusted v2 service is incomplete")
    mcp = FastMCP(
        "Alpha Lab Python Factor Research Service",
        instructions="Submit source text and bounded research requests. Outputs are descriptive only.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )

    @mcp.tool()
    def submit_factor(request: dict) -> dict:
        """Submit factor source content and a strict declarative research window."""
        return service.submit_factor(request)

    @mcp.tool()
    async def run_factor(task_id: str) -> dict:
        """Execute one bounded task through the isolated Worker Broker."""
        return await service.run_factor(task_id)

    @mcp.tool()
    def get_task(task_id: str) -> dict:
        """Read a sanitized task state."""
        task = service.get_task(task_id)
        return task or {"status": "NOT_FOUND"}

    @mcp.tool()
    def get_history(limit: int = 100) -> list[dict]:
        """Read recent task, Evidence and Decision summaries."""
        return service.get_history(limit)

    @mcp.tool()
    def append_decision(decision_id: str, run_id: str, decision: dict) -> dict:
        """Append a descriptive interpretation bound to one immutable Evidence run."""
        return service.append_decision(decision_id=decision_id, run_id=run_id, decision=decision)

    if v2_service is not None:
        @mcp.tool()
        def submit_factor_v2(request: dict) -> dict:
            """Submit a v2 factor against an authorized ResearchRequest."""
            return v2_service.submit_factor_v2(request)

        @mcp.tool()
        async def run_factor_v2(task_id: str) -> dict:
            """Run or resume a v2 factor task through the fixed Worker Broker."""
            return await v2_service.run_factor_v2(task_id)

        @mcp.tool()
        def get_task_v2(task_id: str) -> dict:
            """Read the sanitized status for a v2 task."""
            task = v2_service.get_task_v2(task_id)
            return task or {"status": "NOT_FOUND"}

        @mcp.tool()
        def get_history_v2(limit: int = 100) -> dict:
            """Read normalized history from configured Alpha Lab Stores."""
            return v2_service.get_history_v2(limit)

    app = BearerAuthMiddleware(mcp.streamable_http_app(), bearer_token)
    return mcp, app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ALPHA_LAB_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ALPHA_LAB_MCP_PORT", "8000")))
    args = parser.parse_args(argv)
    factory_ref = os.environ.get("ALPHA_LAB_SERVICE_FACTORY", "")
    token = os.environ.get("ALPHA_LAB_MCP_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("ALPHA_LAB_MCP_TOKEN must be at least 24 characters")
    factory = load_service_factory(factory_ref)
    services = factory()
    profile = os.environ.get("ALPHA_LAB_MCP_PROFILE", "legacy")
    if profile == "legacy":
        _server, app = create_mcp_server(services, bearer_token=token, host=args.host, port=args.port)
    elif profile == "agent":
        agent_gateway = getattr(services, "agent_gateway", None)
        if agent_gateway is None:
            raise SystemExit("trusted Agent gateway dependencies are not configured")
        _server, app = create_agent_mcp_server(
            agent_gateway, bearer_token=token, host=args.host, port=args.port
        )
    else:
        raise SystemExit("ALPHA_LAB_MCP_PROFILE must be legacy or agent")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    main()
