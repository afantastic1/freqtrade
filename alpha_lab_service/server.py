"""MCP Streamable HTTP control plane. Submitted factor code is never imported here."""

from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP

from alpha_lab_service.auth import BearerAuthMiddleware
from alpha_lab_service.runtime import load_service_factory


def create_mcp_server(service, *, bearer_token: str, host: str = "127.0.0.1", port: int = 8000):
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
    service = factory()
    _server, app = create_mcp_server(service, bearer_token=token, host=args.host, port=args.port)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    main()
