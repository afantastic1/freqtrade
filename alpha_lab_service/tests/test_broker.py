import pytest

from alpha_lab_service.broker_server import validate_worker_frames
from alpha_lab_service.worker_profile import WorkerProfile, docker_run_args


def test_broker_accepts_only_protocol_frames_without_host_paths():
    frames = [
        {"type": "init", "protocol_version": "1.0", "source": "code", "symbols": ["AAA"], "params": {}, "seed": 1},
        {"type": "step", "as_of": "2024-01-01T00:00:00Z", "history": [
            {"open_time": "2024-01-01T00:00:00Z", "symbol": "AAA",
             "available_time": "2024-01-01T00:00:00Z", "close": 1.0}
        ]},
        {"type": "end"},
    ]
    assert validate_worker_frames("task-123", frames) is None
    with pytest.raises(ValueError):
        validate_worker_frames("../host", frames)
    with pytest.raises(ValueError):
        validate_worker_frames("task-123", [{**frames[0], "mount": "/:/host"}, *frames[1:]])
    label_frames = [frames[0], {**frames[1], "history": [
        {"open_time": "2024-01-01T00:00:00Z", "symbol": "AAA",
         "available_time": "2024-01-01T00:00:00Z", "forward_return": 0.5}
    ]}, frames[-1]]
    with pytest.raises(ValueError, match="allowlisted"):
        validate_worker_frames("task-123", label_frames)
    malformed = [frames[0], {**frames[1], "history": [
        {"open_time": "2024-01-01T00:00:00Z", "symbol": "AAA",
         "available_time": "2024-01-01T00:00:00Z", "close": "secret"}
    ]}, frames[-1]]
    with pytest.raises(ValueError, match="numeric"):
        validate_worker_frames("task-123", malformed)


def test_broker_invocation_has_fixed_image_command_and_sandbox_limits():
    profile = WorkerProfile(image="sha256:" + "a" * 64)
    args = docker_run_args(profile)
    assert args[-4:] == [profile.image, "python", "-I", "/opt/factor-worker/worker.py"]
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--memory" in args and "--pids-limit" in args
    assert not any("-v" == arg or "--mount" in arg for arg in args)
