import asyncio
import importlib.util
import io
import json
from pathlib import Path

import pytest
import pandas as pd
from pydantic import ValidationError

from alpha_lab_service.broker_client_v2 import BrokerClientV2
from alpha_lab_service.broker_server import WorkerBroker, validate_worker_frames_v2
from alpha_lab_service.worker_protocol_v2 import validate_state_payload, validate_worker_frames_v2
from alpha_lab_service.worker_profile import WorkerProfile

WORKER_PATH = Path(__file__).resolve().parents[2] / "containers/alpha-lab-factor-worker/worker.py"
SPEC = importlib.util.spec_from_file_location("factor_worker_v2", WORKER_PATH)
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


def frames(*, source, timestamps, state=None, stateful=False, warmup_phase="initialize"):
    return [
        {
            "type": "init", "protocol_version": "2.0", "plan_hash": "a" * 64,
            "source": source, "params": {}, "symbols": ["BTCUSDT"],
            "schema_id": "ohlcv-v1", "schema_version": "1",
            "execution_mode": "stateful" if stateful else "stateless",
            "state_schema_id": "json-object-v1" if stateful else None,
            "state_schema_version": "1" if stateful else None,
            "state": state,
        },
        {"type": "warmup", "phase": warmup_phase, "as_of": timestamps[0], "history": []},
        *[
            {"type": "step", "as_of": timestamp, "history": [{
                "open_time": timestamp, "close_time": timestamp,
                "available_time": timestamp, "decision_time": timestamp,
                "symbol": "BTCUSDT", "open": 1.0, "high": 2.0,
                "low": 0.5, "close": 1.5, "volume": 4.0,
            }]}
            for timestamp in timestamps
        ],
        {"type": "end"},
    ]


def invoke_worker(input_frames):
    request = "".join(json.dumps(frame, separators=(",", ":")) + "\n" for frame in input_frames)
    output = io.StringIO()
    WORKER.run_protocol_v2(io.StringIO(request), output)
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_v2_frame_contract_is_separate_and_rejects_overrides():
    input_frames = frames(source="def compute_factor(history, as_of, params): return {'BTCUSDT': 1.0}",
                          timestamps=["2025-01-01T00:05:00Z"])
    assert validate_worker_frames_v2("task-1", "chunk-1", input_frames) is None
    assert validate_worker_frames_v2("task-1", "chunk-1", input_frames) is None
    with pytest.raises(ValueError):
        validate_worker_frames_v2("../host", "chunk-1", input_frames)
    with pytest.raises(ValueError):
        validate_worker_frames_v2("task-1", "../host", input_frames)
    with pytest.raises(ValueError):
        validate_worker_frames_v2("task-1", "chunk-1", [{**input_frames[0], "mount": "/:/host"}, *input_frames[1:]])


def test_v2_state_is_canonical_bounded_json_and_schema_checked():
    assert validate_state_payload({"count": 2}, "json-object-v1", "1") == {"count": 2}
    with pytest.raises(ValueError, match="schema"):
        validate_state_payload({"count": 2}, "unknown", "1")
    with pytest.raises(ValueError, match="size"):
        validate_state_payload({"value": "x" * (1024 * 1024)}, "json-object-v1", "1")
    with pytest.raises(ValueError, match="finite"):
        validate_state_payload({"value": float("nan")}, "json-object-v1", "1")


def test_stateful_restart_does_not_replay_warmup_transition():
    source = """
def initialize_state(warmup, params):
    return {"initialized": 1, "count": len(warmup)}
def compute_factor(history, as_of, params, state):
    updated = {"initialized": state["initialized"], "count": state["count"] + 1}
    return {"BTCUSDT": float(updated["count"])}, updated
"""
    first_times = ["2025-01-01T00:05:00Z", "2025-01-01T00:10:00Z"]
    first_frames = frames(source=source, timestamps=first_times, stateful=True)
    first_frames[1]["history"] = [{
        "open_time": "2025-01-01T00:00:00Z", "close_time": "2025-01-01T00:05:00Z",
        "available_time": "2025-01-01T00:05:00Z", "decision_time": "2025-01-01T00:05:00Z",
        "symbol": "BTCUSDT", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 4.0,
    }]
    first_output = invoke_worker(first_frames)
    checkpoint = first_output[-1]["state"]
    assert checkpoint == {"count": 3, "initialized": 1}

    next_times = ["2025-01-01T00:15:00Z", "2025-01-01T00:20:00Z"]
    next_frames = frames(source=source, timestamps=next_times, stateful=True,
                         state=checkpoint, warmup_phase="read_only")
    next_frames[1]["history"] = first_frames[2]["history"]
    next_output = invoke_worker(next_frames)
    assert next_output[-1]["state"] == {"count": 5, "initialized": 1}
    assert [item["values"]["BTCUSDT"] for item in next_output[1:-1]] == [4.0, 5.0]


def test_stateless_worker_outputs_exact_timestamps_and_ready_count():
    source = "def compute_factor(history, as_of, params): return {'BTCUSDT': float(len(history))}"
    input_frames = frames(source=source, timestamps=["2025-01-01T00:05:00Z", "2025-01-01T00:10:00Z"])
    output = invoke_worker(input_frames)
    assert output[0] == {"type": "ready", "protocol_version": "2.0"}
    assert [pd.Timestamp(frame["as_of"]) for frame in output[1:]] == [
        pd.Timestamp(frame["as_of"]) for frame in input_frames[2:-1]
    ]


def test_worker_rejects_untyped_feature_values_and_frames_after_end():
    source = "def compute_factor(history, as_of, params): return {'BTCUSDT': 1.0}"
    input_frames = frames(source=source, timestamps=["2025-01-01T00:05:00Z"])
    input_frames[2]["history"][0]["close"] = "not-a-number"
    with pytest.raises(ValueError, match="numbers"):
        invoke_worker(input_frames)
    input_frames = frames(source=source, timestamps=["2025-01-01T00:05:00Z"])
    input_frames.append({"type": "step", "as_of": "2025-01-01T00:10:00Z", "history": []})
    with pytest.raises(ValueError, match="after end"):
        invoke_worker(input_frames)


def test_v2_broker_client_uses_fixed_v2_route_and_rejects_bad_frames(monkeypatch):
    calls = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"result":[{"type":"ready","protocol_version":"2.0"}]}'

    def fake_urlopen(request, timeout):
        calls["url"] = request.full_url
        calls["body"] = json.loads(request.data)
        calls["timeout"] = timeout
        return Response()

    monkeypatch.setattr("alpha_lab_service.broker_client_v2.urllib.request.urlopen", fake_urlopen)
    client = BrokerClientV2("http://127.0.0.1:9000", "x" * 24)
    input_frames = frames(source="def compute_factor(history, as_of, params): return {'BTCUSDT': 1.0}",
                          timestamps=["2025-01-01T00:05:00Z"])
    result = asyncio.run(client.run_chunk("task-1", "chunk-1", input_frames))
    assert result == [{"type": "ready", "protocol_version": "2.0"}]
    assert calls["url"] == "http://127.0.0.1:9000/v2/run-chunk"
    assert set(calls["body"]) == {"task_id", "chunk_id", "frames"}
    with pytest.raises(ValueError):
        validate_worker_frames_v2("task-1", "chunk-1", [{**input_frames[0], "image": "override"}, *input_frames[1:]])


def test_v2_broker_fails_closed_when_pinned_image_lacks_protocol_support():
    profile = WorkerProfile(image="sha256:" + "a" * 64, protocol_versions=("1.0",))
    broker = WorkerBroker(profile)
    input_frames = frames(source="def compute_factor(history, as_of, params): return {'BTCUSDT': 1.0}",
                          timestamps=["2025-01-01T00:05:00Z"])
    with pytest.raises(RuntimeError, match="protocol 2.0"):
        broker.run_frames_v2("task-1", "chunk-1", input_frames)
