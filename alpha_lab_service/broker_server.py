"""Narrow separate-process broker. Only this process invokes Docker."""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from alpha_lab_service.protocol import MAX_SOURCE_BYTES
from alpha_lab_service.worker_profile import WorkerProfile, docker_run_args, profile_from_file

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_STEPS = 512
MAX_CONCURRENCY = 2
ALLOWED_ROW_FIELDS = {"open_time", "symbol", "available_time", "open", "high", "low", "close", "volume"}


def validate_worker_frames(task_id: str, frames: list[dict]) -> None:
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise ValueError("invalid task id")
    if not isinstance(frames, list) or len(frames) < 3 or len(frames) > MAX_STEPS + 2:
        raise ValueError("worker frames outside limits")
    init, *steps, end = frames
    if set(init) != {"type", "protocol_version", "source", "symbols", "params", "seed"} or init.get("type") != "init":
        raise ValueError("invalid init frame")
    if init.get("protocol_version") != "1.0" or not isinstance(init.get("source"), str):
        raise ValueError("invalid init contract")
    if len(init["source"].encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds limit")
    if (not isinstance(init["symbols"], list) or not 1 <= len(init["symbols"]) <= 100
            or len(set(init["symbols"])) != len(init["symbols"])):
        raise ValueError("invalid symbol set")
    if not isinstance(init.get("params"), dict) or any(
        isinstance(value, (dict, list)) or value is None for value in init["params"].values()
    ):
        raise ValueError("parameters must be JSON scalars")
    if set(end) != {"type"} or end.get("type") != "end":
        raise ValueError("invalid end frame")
    if not steps:
        raise ValueError("at least one step is required")
    symbols = set(init["symbols"])
    for step in steps:
        if set(step) != {"type", "as_of", "history"} or step.get("type") != "step":
            raise ValueError("invalid step frame")
        if not isinstance(step["history"], list):
            raise ValueError("invalid history payload")
        try:
            as_of = __import__("datetime").datetime.fromisoformat(step["as_of"].replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("as_of must be an aware timestamp") from exc
        if as_of.tzinfo is None or not step["history"] or len(step["history"]) > 10000:
            raise ValueError("step history outside limits")
        rows_seen = set()
        section_symbols: dict[str, set[str]] = {}
        row_order = []
        for row in step["history"]:
            if not isinstance(row, dict) or not set(row).issubset(ALLOWED_ROW_FIELDS):
                raise ValueError("Worker history rows must use allowlisted fields")
            if not {"open_time", "symbol", "available_time"}.issubset(row):
                raise ValueError("Worker row missing time and symbol metadata")
            for feature in set(row) & {"open", "high", "low", "close", "volume"}:
                value = row[feature]
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                ):
                    raise ValueError("Worker feature values must be finite numeric scalars or null")
            try:
                opened = __import__("datetime").datetime.fromisoformat(row["open_time"].replace("Z", "+00:00"))
                available = __import__("datetime").datetime.fromisoformat(row["available_time"].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("Worker row timestamps must be aware") from exc
            if (opened.tzinfo is None or available.tzinfo is None or opened > as_of
                    or available > as_of or available < opened):
                raise ValueError("Worker row violates point-in-time cutoff")
            symbol = row["symbol"]
            key = (opened, symbol)
            if symbol not in symbols or key in rows_seen:
                raise ValueError("Worker row has undeclared or duplicate symbol")
            rows_seen.add(key)
            row_order.append(key)
            section_symbols.setdefault(row["open_time"], set()).add(symbol)
        if row_order != sorted(row_order) or any(assets != symbols for assets in section_symbols.values()):
            raise ValueError("Worker history is not sorted and cross-asset aligned")
    encoded = "".join(json.dumps(frame, separators=(",", ":"), allow_nan=False) + "\n" for frame in frames).encode()
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError("worker input exceeds byte limit")


class WorkerBroker:
    def __init__(self, profile: WorkerProfile, *, max_concurrency: int = MAX_CONCURRENCY):
        if not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise ValueError("broker concurrency exceeds limit")
        self.profile = profile
        self._slots = threading.BoundedSemaphore(max_concurrency)

    def run_frames(self, task_id: str, frames: list[dict]) -> list[dict]:
        validate_worker_frames(task_id, frames)
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("broker capacity exhausted")
        try:
            return self._run_container(task_id, frames)
        finally:
            self._slots.release()

    def _run_container(self, task_id: str, frames: list[dict]) -> list[dict]:
        payload = "".join(json.dumps(frame, separators=(",", ":"), allow_nan=False) + "\n" for frame in frames)
        with tempfile.TemporaryDirectory(prefix="alpha-worker-") as temp_dir:
            cidfile = Path(temp_dir) / "container.id"
            args = docker_run_args(self.profile)
            args[-4:-4] = ["--cidfile", str(cidfile), "--label", f"alpha-lab-task={task_id}"]
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            output = bytearray()
            oversized = threading.Event()

            def drain_stdout():
                assert process.stdout is not None
                while chunk := process.stdout.read(65536):
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        oversized.set()
                        process.kill()
                        continue
                    output.extend(chunk)

            reader = threading.Thread(target=drain_stdout, daemon=True)
            reader.start()
            try:
                assert process.stdin is not None
                process.stdin.write(payload.encode("utf-8"))
                process.stdin.close()
                process.wait(timeout=self.profile.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                self._kill_container(cidfile)
                raise TimeoutError("Worker exceeded wall-clock limit") from exc
            except BrokenPipeError as exc:
                process.wait(timeout=2)
                raise RuntimeError("Worker rejected request") from exc
            finally:
                reader.join(timeout=5)
            if oversized.is_set():
                self._kill_container(cidfile)
                raise RuntimeError("Worker output exceeded byte limit")
            if process.returncode != 0:
                raise RuntimeError("Worker failed in isolated runtime")
            try:
                result = [json.loads(line) for line in output.decode("utf-8").splitlines() if line]
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Worker emitted invalid protocol output") from exc
            if not result or result[0].get("type") != "ready" or len(result) != len(frames) - 1:
                raise RuntimeError("Worker output does not match request steps")
            return result

    @staticmethod
    def _kill_container(cidfile: Path) -> None:
        try:
            container_id = cidfile.read_text(encoding="ascii").strip()
        except OSError:
            return
        if re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            subprocess.run(["docker", "kill", container_id], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)


def make_handler(broker: WorkerBroker, token: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/run":
                self.send_error(404)
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {token}"):
                self.send_error(401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_FRAME_BYTES + 1024:
                self.send_error(413)
                return
            try:
                request = json.loads(self.rfile.read(length))
                if set(request) != {"task_id", "frames"}:
                    raise ValueError("unexpected request fields")
                result = broker.run_frames(request["task_id"], request["frames"])
                body = json.dumps({"result": result}, separators=(",", ":")).encode()
                self.send_response(200)
            except TimeoutError:
                body = b'{"error":"TIMED_OUT"}'
                self.send_response(408)
            except (ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError):
                body = b'{"error":"EXECUTION_BLOCKED"}'
                self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="containers/alpha-lab-factor-worker/profile.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    token = os.environ.get("ALPHA_LAB_BROKER_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("ALPHA_LAB_BROKER_TOKEN must be at least 24 characters")
    profile = profile_from_file(Path(args.profile))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(WorkerBroker(profile), token))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
