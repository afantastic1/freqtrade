"""Actual Docker profile tests. The image must be built; missing runtime fails closed."""

import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

from alpha_lab_service.worker_profile import WorkerProfile, docker_run_args
from alpha_lab_service.broker_server import WorkerBroker


IMAGE = os.environ.get("ALPHA_LAB_WORKER_IMAGE", "")


def _run(source: str, *, timeout: int = 20):
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    profile = WorkerProfile(image=IMAGE, timeout_seconds=timeout)
    frames = [
        {"type": "init", "protocol_version": "1.0", "source": source, "symbols": ["AAA", "BBB"], "params": {}, "seed": 1},
        {"type": "step", "as_of": "2024-01-02T00:00:00Z", "history": [
            {"open_time": "2024-01-01T00:00:00Z", "symbol": "AAA", "available_time": "2024-01-01T01:00:00Z", "close": 10.0},
            {"open_time": "2024-01-01T00:00:00Z", "symbol": "BBB", "available_time": "2024-01-01T01:00:00Z", "close": 20.0},
        ]},
        {"type": "end"},
    ]
    proc = subprocess.run(
        docker_run_args(profile), input="".join(json.dumps(frame) + "\n" for frame in frames),
        text=True, capture_output=True, timeout=timeout + 10, check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return [json.loads(line) for line in proc.stdout.splitlines()]


def test_container_denies_network_and_has_no_sensitive_mounts():
    result = _run("""
import pandas as pd, socket, os
def compute_factor(history, *, as_of, params):
    network_denied = 1.0
    try:
        socket.create_connection(('1.1.1.1', 53), timeout=1)
        network_denied = 0.0
    except OSError:
        pass
    sensitive_absent = all(not os.path.exists(p) for p in (
        '/var/run/docker.sock', '/alpha_lab', '/user_data', '/home/von',
        '/opt/alpha-lab-core', '/mnt/holdout', '/srv/research-store.sqlite3'))
    try:
        open('/opt/factor-worker/forbidden', 'w').write('bad')
        write_denied = 0.0
    except OSError:
        write_denied = 1.0
    seccomp_active = any(line.startswith('Seccomp:') and line.split()[-1] == '2' for line in open('/proc/self/status'))
    uid_is_worker = os.getuid() == 65532
    cap_drop_all = any(line.startswith('CapEff:') and int(line.split()[1], 16) == 0 for line in open('/proc/self/status'))
    apparmor_confined = 'unconfined' not in open('/proc/self/attr/current').read()
    value = float(network_denied and sensitive_absent and write_denied and seccomp_active and uid_is_worker and cap_drop_all and apparmor_confined)
    return pd.Series({symbol: value for symbol in history.index.get_level_values('symbol').unique()})
""")
    assert result[0]["type"] == "ready"
    assert set(result[1]["values"].values()) == {1.0}


def test_container_data_and_dependency_surface_support_numerical_factor():
    result = _run("""
import numpy as np, pandas as pd, scipy.fft, pywt
def compute_factor(history, *, as_of, params):
    close = history['close'].unstack('symbol')
    spectrum = np.abs(scipy.fft.rfft(close.to_numpy(), axis=0))
    coeffs = pywt.wavedec(close.to_numpy()[:, 0], 'haar', level=1)
    signal = float(spectrum.sum() + np.abs(coeffs[-1]).sum())
    return pd.Series({s: signal for s in close.columns})
""")
    assert result[0]["type"] == "ready"
    assert result[1]["values"] == {"AAA": result[1]["values"]["AAA"], "BBB": result[1]["values"]["AAA"]}


def _frames(source):
    return [
        {"type": "init", "protocol_version": "1.0", "source": source, "symbols": ["AAA", "BBB"], "params": {}, "seed": 0},
        {"type": "step", "as_of": "2024-01-02T00:00:00Z", "history": [
            {"open_time": "2024-01-01T00:00:00Z", "symbol": "AAA", "available_time": "2024-01-01T01:00:00Z", "close": 10.0},
            {"open_time": "2024-01-01T00:00:00Z", "symbol": "BBB", "available_time": "2024-01-01T01:00:00Z", "close": 20.0},
        ]},
        {"type": "end"},
    ]


def test_broker_enforces_container_pid_limit():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    source = """
import os, time, pandas as pd
def compute_factor(history, *, as_of, params):
    children = []
    for _ in range(100):
        try:
            pid = os.fork()
        except OSError:
            break
        if pid == 0:
            time.sleep(0.1)
            os._exit(0)
        children.append(pid)
    for pid in children:
        os.waitpid(pid, 0)
    return pd.Series({symbol: float(len(children)) for symbol in history.index.get_level_values('symbol').unique()})
"""
    result = WorkerBroker(WorkerProfile(image=IMAGE)).run_frames("pid-limit", _frames(source))
    assert 1 < result[1]["values"]["AAA"] < 64


def test_broker_kills_worker_at_wall_clock_limit():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    profile = WorkerProfile(image=IMAGE, timeout_seconds=2)
    with pytest.raises(TimeoutError, match="wall-clock"):
        WorkerBroker(profile).run_frames("timeout", _frames("""
def compute_factor(history, *, as_of, params):
    while True:
        pass
"""))


def test_broker_contains_memory_exhaustion_with_container_limit():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    with pytest.raises(RuntimeError, match="Worker failed"):
        WorkerBroker(WorkerProfile(image=IMAGE)).run_frames("memory-limit", _frames("""
import numpy as np, pandas as pd
def compute_factor(history, *, as_of, params):
    values = np.ones(1024 * 1024 * 1024, dtype=np.uint8)
    return pd.Series({symbol: float(values.sum()) for symbol in history.index.get_level_values('symbol').unique()})
"""))


def test_tmp_scratch_does_not_leak_between_worker_tasks():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    broker = WorkerBroker(WorkerProfile(image=IMAGE))
    writer = broker.run_frames("scratch-writer", _frames("""
import pandas as pd
def compute_factor(history, *, as_of, params):
    open('/tmp/task-marker', 'w').write('present')
    return pd.Series({symbol: 1.0 for symbol in history.index.get_level_values('symbol').unique()})
"""))
    reader = broker.run_frames("scratch-reader", _frames("""
import os, pandas as pd
def compute_factor(history, *, as_of, params):
    present = os.path.exists('/tmp/task-marker')
    return pd.Series({symbol: float(present) for symbol in history.index.get_level_values('symbol').unique()})
"""))
    assert writer[1]["values"] == {"AAA": 1.0, "BBB": 1.0}
    assert reader[1]["values"] == {"AAA": 0.0, "BBB": 0.0}


def test_broker_stops_unbounded_protocol_output():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must name the locally built immutable image ID")
    with pytest.raises(RuntimeError, match="output exceeded"):
        WorkerBroker(WorkerProfile(image=IMAGE)).run_frames("output-limit", _frames("""
import os, pandas as pd
def compute_factor(history, *, as_of, params):
    os.write(1, b'x' * (5 * 1024 * 1024))
    return pd.Series({symbol: 1.0 for symbol in history.index.get_level_values('symbol').unique()})
"""))
