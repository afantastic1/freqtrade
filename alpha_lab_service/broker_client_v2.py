"""Fixed-operation HTTP client for the isolated v2 Worker Broker endpoint."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from alpha_lab_service.worker_protocol_v2 import MAX_FRAME_BYTES


class BrokerClientV2:
    def __init__(self, endpoint: str, token: str, *, timeout: float = 90):
        if not endpoint.startswith("http://127.0.0.1:") and not endpoint.startswith("https://"):
            raise ValueError("broker endpoint must be loopback or HTTPS")
        if len(token) < 24:
            raise ValueError("broker token must be at least 24 characters")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, task_id: str, chunk_id: str, frames: list[dict]) -> list[dict]:
        body = json.dumps(
            {"task_id": task_id, "chunk_id": chunk_id, "frames": frames},
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES + 1024:
            raise ValueError("v2 Broker request exceeds byte limit")
        request = urllib.request.Request(
            self.endpoint + "/v2/run-chunk", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except (OSError, json.JSONDecodeError):
                payload = {}
            error = payload.get("error", "v2 Broker rejected request")
            category = payload.get("category")
            suffix = f" ({category})" if isinstance(category, str) else ""
            raise RuntimeError(f"{error}{suffix}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("v2 Broker unavailable") from exc
        if "result" not in payload:
            raise RuntimeError(payload.get("error", "v2 Broker execution failed"))
        return payload["result"]

    async def run_chunk(self, task_id: str, chunk_id: str, frames: list[dict]) -> list[dict]:
        return await asyncio.to_thread(self._request, task_id, chunk_id, frames)
