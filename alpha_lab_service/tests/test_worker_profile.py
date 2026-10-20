import json
from pathlib import Path

import pytest

from alpha_lab_service.worker_profile import WorkerProfile, docker_run_args, profile_from_file


def test_worker_profile_is_digest_pinned_and_cannot_add_host_capabilities():
    with pytest.raises(ValueError, match="digest"):
        WorkerProfile(image="latest")
    with pytest.raises(ValueError, match="host mounts"):
        WorkerProfile(image="sha256:" + "a" * 64, mounts=("/:/host",))
    with pytest.raises(ValueError, match="limits"):
        WorkerProfile(image="sha256:" + "a" * 64, memory_limit="16g")
    with pytest.raises(ValueError, match="UID"):
        WorkerProfile(image="sha256:" + "a" * 64, user="0:0")
    args = docker_run_args(WorkerProfile(image="sha256:" + "a" * 64))
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert not any(arg.startswith("seccomp=") for arg in args), "use Docker's stricter built-in seccomp allowlist"
    assert "--security-opt=apparmor=docker-default" in args
    assert "nodev" in next(arg for arg in args if arg.startswith("/tmp:"))
    assert not any("/var/run/docker.sock" in arg for arg in args)


def test_profile_file_cannot_weaken_read_only_or_default_security_controls(tmp_path):
    profile = json.loads((Path(__file__).resolve().parents[2] / "containers/alpha-lab-factor-worker/profile.json").read_text())
    profile["worker_image"] = "sha256:" + "a" * 64
    profile["apparmor"] = "unconfined"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="security controls"):
        profile_from_file(path)
