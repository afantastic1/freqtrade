import asyncio
import json

import pandas as pd
import pytest
import httpx

from alpha_lab_service.auth import BearerAuthMiddleware
from alpha_lab_service.jobs import ResearchService
from alpha_lab_service.server import create_mcp_server
from alpha_lab_service.store import ResearchStoreV1

TOKEN = "test-token-0123456789-abcdefghijkl"


class FakeBroker:
    def __init__(self):
        self.calls = []

    async def run_frames(self, task_id, frames):
        self.calls.append((task_id, frames))
        steps = [frame for frame in frames if frame["type"] == "step"]
        return [{"type": "ready"}] + [
            {"as_of": step["as_of"], "values": {"AAA": 1.0, "BBB": 2.0}, "diagnostics": {}, "output_hash": f"{i + 1:064x}"}
            for i, step in enumerate(steps)
        ]


def _request():
    return {
        "source": "raise RuntimeError('must only run in Worker')\ndef compute_factor(history, *, as_of, params): return history.close",
        "hypothesis": "Synthetic test source",
        "symbols": ["AAA", "BBB"],
        "timeframe": "1d",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-03T00:00:00Z",
        "params": {},
        "max_lookback": 2,
    }


def _service(tmp_path):
    broker = FakeBroker()
    frame_provider = lambda request: [
        {"type": "step", "as_of": "2024-01-01T00:00:00Z", "history": [
            {"open_time": "2024-01-01T00:00:00Z", "symbol": symbol,
             "available_time": "2024-01-01T00:00:00Z", "close": 1.0}
            for symbol in ("AAA", "BBB")
        ]},
        {"type": "step", "as_of": "2024-01-02T00:00:00Z", "history": [
            {"open_time": timestamp, "symbol": symbol,
             "available_time": timestamp, "close": 1.0}
            for timestamp in ("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")
            for symbol in ("AAA", "BBB")
        ]},
    ]
    index = pd.MultiIndex.from_product([
        pd.date_range("2024-01-01", periods=2, tz="UTC"), ["AAA", "BBB"]
    ], names=["open_time", "symbol"])
    labels = pd.Series([0.1, 0.2, 0.2, 0.1], index=index)
    service = ResearchService(
        ResearchStoreV1(tmp_path / "service.sqlite3"), broker,
        frame_provider=frame_provider,
        labels_provider=lambda request, output_index: labels.reindex(output_index),
        data_fingerprint="synthetic-sha", image_digest="sha256:" + "a" * 64,
        engine_identity="engine-v1",
    )
    return service, broker


def test_mcp_registers_narrow_tools_and_submit_does_not_execute_source(tmp_path):
    service, broker = _service(tmp_path)
    server, _app = create_mcp_server(service, bearer_token=TOKEN)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == {"submit_factor", "run_factor", "get_task", "get_history", "append_decision"}
    submitted = service.submit_factor(_request())
    assert submitted["status"] == "SUBMITTED"
    assert broker.calls == []


def test_repeated_submit_is_idempotent_and_run_writes_only_descriptive_evidence(tmp_path):
    service, broker = _service(tmp_path)
    first = service.submit_factor(_request())
    second = service.submit_factor(_request())
    assert first["task_id"] == second["task_id"]
    result = asyncio.run(service.run_factor(first["task_id"]))
    assert result["status"] in {"EXPLORATORY", "DIAGNOSTIC"}, result
    assert result["evidence_hash"]
    assert "p_two_sided" not in json.dumps(result)
    assert "SUPPORTED" not in json.dumps(result)
    assert len(broker.calls) == 1
    decision = service.append_decision(
        decision_id="decision-1", run_id=result["run_id"],
        decision={"status": "DIAGNOSTIC", "summary": "Synthetic only"},
    )
    assert decision["status"] == "DIAGNOSTIC"
    with pytest.raises(ValueError, match="descriptive"):
        service.append_decision(
            decision_id="decision-2", run_id=result["run_id"],
            decision={"status": "SUPPORTED"},
        )


def test_mcp_authentication_rejects_missing_and_wrong_tokens():
    async def check(token):
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "path": "/mcp", "headers": [] if token is None else [(b"authorization", f"Bearer {token}".encode())]}
        await BearerAuthMiddleware(lambda *_: None, TOKEN)(scope, receive, send)
        return sent
    assert asyncio.run(check(None))[0]["status"] == 401
    assert asyncio.run(check("wrong"))[0]["status"] == 401


def test_streamable_http_endpoint_requires_bearer_and_accepts_initialize(tmp_path):
    service, _ = _service(tmp_path)
    _server, app = create_mcp_server(service, bearer_token=TOKEN)
    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "alpha-lab-test", "version": "1"},
        },
    }
    async def exercise_http():
        starlette_app = app.app
        async with starlette_app.router.lifespan_context(starlette_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client:
                denied = await client.post("/mcp", json=initialize)
                assert denied.status_code == 401
                response = await client.post(
                    "/mcp", json=initialize,
                    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"},
                )
                assert response.status_code == 200
                message = json.loads(response.text.split("data: ", 1)[1].splitlines()[0])
                assert message["result"]["protocolVersion"] == "2025-03-26"
    asyncio.run(exercise_http())


def test_request_cannot_supply_paths_images_or_commands(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        service.submit_factor({**_request(), "command": "id"})
