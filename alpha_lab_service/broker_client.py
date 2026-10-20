"""HTTP client for the separately deployed fixed-operation Broker."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request


class BrokerClient:
    def __init__(self, endpoint: str, token: str, *, timeout: float = 90):
        if not endpoint.startswith("http://127.0.0.1:") and not endpoint.startswith("https://"):
            raise ValueError("broker endpoint must be loopback or HTTPS")
        if len(token) < 24:
            raise ValueError("broker token must be at least 24 characters")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, task_id: str, frames: list[dict]) -> list[dict]:
        body = json.dumps({"task_id": task_id, "frames": frames}, separators=(",", ":"), allow_nan=False).encode()
        request = urllib.request.Request(
            self.endpoint + "/v1/run", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Broker unavailable") from exc
        if "result" not in payload:
            raise RuntimeError(payload.get("error", "Broker execution failed"))
        return payload["result"]

    async def run_frames(self, task_id: str, frames: list[dict]) -> list[dict]:
        return await asyncio.to_thread(self._request, task_id, frames)
