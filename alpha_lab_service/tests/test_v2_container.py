"""Actual-container checks for the v2 protocol against the pinned Worker image."""

import os

import pytest

from alpha_lab_service.broker_server import WorkerBroker
from alpha_lab_service.worker_profile import WorkerProfile


IMAGE = os.environ.get("ALPHA_LAB_WORKER_IMAGE", "")


def frames(source, *, stateful=False, state=None, warmup_phase="initialize"):
    timestamp = "2025-05-01T00:05:30Z"
    return [
        {"type": "init", "protocol_version": "2.0", "plan_hash": "a" * 64,
         "source": source, "params": {}, "symbols": ["BTCUSDT"],
         "schema_id": "ohlcv-v1", "schema_version": "1",
         "execution_mode": "stateful" if stateful else "stateless",
         "state_schema_id": "json-object-v1" if stateful else None,
         "state_schema_version": "1" if stateful else None, "state": state},
        {"type": "warmup", "phase": warmup_phase, "as_of": timestamp, "history": []},
        {"type": "step", "as_of": timestamp, "history": [{
            "open_time": "2025-05-01T00:00:00Z", "close_time": "2025-05-01T00:05:00Z",
            "available_time": timestamp, "decision_time": timestamp, "symbol": "BTCUSDT",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1.0,
        }]},
        {"type": "end"},
    ]


def broker(*, timeout=60, image=IMAGE):
    if not image.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must identify the pinned local Worker image")
    return WorkerBroker(WorkerProfile(image=image, timeout_seconds=timeout, protocol_versions=("1.0", "2.0")))


def test_v2_container_denies_network_socket_protected_data_and_root_writes():
    source = """
import os, socket
def compute_factor(history, *, as_of, params):
    try:
        socket.create_connection(('1.1.1.1', 53), timeout=1)
        network_denied = False
    except OSError:
        network_denied = True
    try:
        sock = socket.socket(socket.AF_UNIX)
        sock.connect('/var/run/docker.sock')
        sock.close()
        docker_socket_denied = False
    except OSError:
        docker_socket_denied = True
    absent = all(not os.path.exists(path) for path in (
        '/var/run/docker.sock', '/alpha_lab', '/user_data', '/home/von',
        '/mnt/holdout', '/srv/research-store.sqlite3'))
    try:
        open('/opt/factor-worker/forbidden', 'w').write('x')
        write_denied = False
    except OSError:
        write_denied = True
    status = open('/proc/self/status').read()
    uid_ok = os.getuid() == 65532
    cap_ok = any(line.startswith('CapEff:') and int(line.split()[1], 16) == 0 for line in status.splitlines())
    seccomp_ok = any(line.startswith('Seccomp:') and line.split()[-1] == '2' for line in status.splitlines())
    value = float(network_denied and docker_socket_denied and absent and write_denied and uid_ok and cap_ok and seccomp_ok)
    return {'BTCUSDT': value}
"""
    result = broker().run_frames_v2("v2-security", "chunk-00000000", frames(source))
    assert result[0] == {"type": "ready", "protocol_version": "2.0"}
    assert result[1]["values"] == {"BTCUSDT": 1.0}


def test_v2_container_enforces_tmpfs_isolation_and_worker_limits():
    worker = broker()
    result = worker.run_frames_v2("v2-tmp-writer", "chunk-00000000", frames(
        "def compute_factor(history, *, as_of, params):\n open('/tmp/marker', 'w').write('x')\n return {'BTCUSDT': 1.0}"
    ))
    assert result[1]["values"] == {"BTCUSDT": 1.0}
    readback = worker.run_frames_v2("v2-tmp-reader", "chunk-00000000", frames(
        "import os\ndef compute_factor(history, *, as_of, params):\n return {'BTCUSDT': float(os.path.exists('/tmp/marker'))}"
    ))
    assert readback[1]["values"] == {"BTCUSDT": 0.0}
    profile = WorkerProfile(image=IMAGE)
    assert profile.network == "none" and profile.mounts == ()
    assert profile.read_only and profile.user == "65532:65532"
    assert profile.memory_limit == "512m" and profile.pids_limit == 64
    assert profile.nano_cpus == 1_000_000_000 and profile.tmpfs_size == "16m"


def test_v2_container_enforces_process_and_memory_caps():
    source = """
import os, time
def compute_factor(history, *, as_of, params):
    children = []
    for _ in range(100):
        try:
            child = os.fork()
        except OSError:
            break
        if child == 0:
            time.sleep(0.1)
            os._exit(0)
        children.append(child)
    for child in children:
        os.waitpid(child, 0)
    return {'BTCUSDT': float(len(children))}
"""
    result = broker().run_frames_v2("v2-pid-limit", "chunk-00000000", frames(source))
    assert 1 < result[1]["values"]["BTCUSDT"] < 64
    with pytest.raises(RuntimeError, match="Worker failed"):
        broker().run_frames_v2("v2-memory-limit", "chunk-00000000", frames("""
import numpy as np
def compute_factor(history, *, as_of, params):
    payload = np.ones(1024 * 1024 * 1024, dtype=np.uint8)
    return {'BTCUSDT': float(payload.sum())}
"""))


def test_v2_container_times_out_and_missing_runtime_fails_closed():
    with pytest.raises(TimeoutError, match="wall-clock"):
        broker(timeout=2).run_frames_v2("v2-timeout", "chunk-00000000", frames(
            "def compute_factor(history, *, as_of, params):\n while True: pass"
        ))
    missing = "sha256:" + "0" * 64
    with pytest.raises(RuntimeError, match="Worker failed"):
        broker(image=missing).run_frames_v2("v2-missing-image", "chunk-00000000", frames(
            "def compute_factor(history, *, as_of, params): return {'BTCUSDT': 1.0}"
        ))


def test_v2_container_broker_rejects_malformed_frames_before_launch():
    request = frames("def compute_factor(history, *, as_of, params): return {'BTCUSDT': 1.0}")
    request[0]["mount"] = "/:/host"
    with pytest.raises(ValueError, match="invalid v2 init frame"):
        broker().run_frames_v2("v2-malformed", "chunk-00000000", request)
