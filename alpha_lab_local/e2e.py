"""Exercise the seven real Agent MCP tools and verify immutable local Evidence."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from alpha_lab_local.config import POLICY_SHA256

EXPECTED_TOOLS = {
    "get_data_coverage", "create_research_request_v2", "submit_factor_v2",
    "run_factor_v2", "get_task_status", "get_task_report", "get_history_v2",
}
FACTOR_SOURCE = """
def compute_factor(history, *, as_of, params):
    lookback = int(params["lookback"])
    close = history["close"]
    if len(close) <= lookback:
        return {"BTCUSDT": 0.0}
    return {"BTCUSDT": float(close.iloc[-1] / close.iloc[-lookback - 1] - 1.0)}
"""

def _tool_value(result):
    if result.isError:
        raise RuntimeError("MCP tool returned an error")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for item in result.content:
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except json.JSONDecodeError:
                return item.text
    return None

async def run_acceptance(endpoint: str, token: str, store_path: Path) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(
        endpoint, headers=headers, timeout=timedelta(seconds=60),
        sse_read_timeout=timedelta(hours=1),
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool_names = {item.name for item in (await session.list_tools()).tools}
            if tool_names != EXPECTED_TOOLS:
                raise RuntimeError("Agent MCP tool profile differs from the seven-tool contract")

            async def call(name, arguments):
                return _tool_value(await session.call_tool(
                    name, arguments, read_timeout_seconds=timedelta(hours=1)
                ))

            coverage = await call("get_data_coverage", {
                "dataset_key": "binance:futures:BTC:ohlcv:5m",
                "start": "2024-01-01T00:00:00Z", "end": "2024-01-08T00:00:00Z",
            })
            if coverage.get("status") != "AVAILABLE":
                raise RuntimeError(f"BTC Discovery Catalog coverage unavailable: {coverage.get('status')}")

            request = await call("create_research_request_v2", {"request": {
                "protocol_version": "2.0-research-request",
                "request_id": f"alpha-local-btc-5m-{uuid.uuid4().hex}",
                "campaign_id": "dataset_builder_acceptance",
                "policy_version": "2",
                "campaign_policy_sha256": POLICY_SHA256,
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-08T00:00:00Z",
                "symbols": ["BTCUSDT"], "timeframe": "5m",
                "mode": "time_series", "data_types": ["futures_ohlcv"],
                "split": "discovery", "lookback_bars": 20,
                "label_horizon_bars": 3, "embargo_bars": 1,
            }})
            if request.get("status") != "AUTHORIZED":
                raise RuntimeError("BTC Discovery ResearchRequest was not authorized")

            submitted = await call("submit_factor_v2", {
                "research_request_id": request["request_id"], "source": FACTOR_SOURCE,
                "params": {"lookback": 12}, "execution_mode": "stateless",
            })
            task_id = submitted["task_id"]
            execution = await call("run_factor_v2", {"task_id": task_id})
            status = await call("get_task_status", {"task_id": task_id})
            report = await call("get_task_report", {"task_id": task_id})
            history = await call("get_history_v2", {"limit": 20})

    if status.get("status") != "COMPLETED" or not report.get("evidence_hash"):
        raise RuntimeError(f"real Worker execution did not complete: {status.get('status')}")
    if report.get("formal_inference_status") != "BLOCKED":
        raise RuntimeError("formal inference must remain blocked for descriptive evidence")
    with sqlite3.connect(f"file:{store_path.resolve()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT task_id, run_id, evidence_hash, evidence_json FROM evidence WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("completed MCP task has no persistent Evidence Store row")
    task_from_store, run_id, evidence_hash, evidence_json = row
    evidence = json.loads(evidence_json)
    if (task_from_store != task_id or report["evidence_hash"] != evidence_hash
            or report["run_id"] != run_id or evidence.get("worker_image_digest") is None
            or evidence.get("dataset_manifest_sha256") is None):
        raise RuntimeError("MCP report does not match persistent Worker Evidence")
    return {
        "status": "PASS", "mcp_transport": "Streamable HTTP",
        "tools_called": sorted(EXPECTED_TOOLS), "catalog_coverage": coverage["status"],
        "campaign_id": request["campaign_id"],
        "campaign_policy_sha256": request["campaign_policy_sha256"],
        "interval": [request["start"], request["end"]], "timeframe": request["timeframe"],
        "task_id": task_id, "request_hash": status.get("request_hash"),
        "run_id": run_id, "evidence_hash": evidence_hash,
        "worker_image_digest": evidence["worker_image_digest"],
        "evidence_status": evidence.get("status"),
        "formal_inference_status": evidence.get("formal_inference_status"),
        "descriptive_report": report.get("report"),
        "history_entry_count": len(history.get("entries", [])),
        "execution_status": execution.get("status"),
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("ALPHA_LAB_MCP_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("ALPHA_LAB_MCP_TOKEN must be supplied by the operator")
    result = asyncio.run(run_acceptance(args.endpoint, token, args.store))
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
