"""Immutable server-owned Docker profile for untrusted factor workers."""

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class WorkerProfile:
    image: str
    command: tuple[str, ...] = ("python", "-I", "/opt/factor-worker/worker.py")
    network: str = "none"
    user: str = "65532:65532"
    read_only: bool = True
    cap_drop: tuple[str, ...] = ("ALL",)
    pids_limit: int = 64
    memory_limit: str = "512m"
    nano_cpus: int = 1_000_000_000
    tmpfs_size: str = "16m"
    timeout_seconds: int = 60
    mounts: tuple[str, ...] = ()
    protocol_versions: tuple[str, ...] = ("1.0",)

    def __post_init__(self):
        if self.image == "BUILD_REQUIRED" or (not self.image.startswith("sha256:") and "@sha256:" not in self.image):
            raise ValueError("worker image must be pinned by digest")
        if self.network != "none" or self.mounts:
            raise ValueError("worker profile must disable networking and host mounts")
        if self.command != ("python", "-I", "/opt/factor-worker/worker.py"):
            raise ValueError("worker command is fixed")
        if not self.protocol_versions or not set(self.protocol_versions).issubset({"1.0", "2.0"}):
            raise ValueError("worker protocol versions are unsupported")
        if self.user != "65532:65532":
            raise ValueError("worker must use the fixed unprivileged UID")
        if not self.read_only or self.cap_drop != ("ALL",):
            raise ValueError("worker profile must use a read-only root and drop all capabilities")
        if self.memory_limit != "512m" or not 0 < self.nano_cpus <= 1_000_000_000 or self.tmpfs_size != "16m":
            raise ValueError("worker resource limits exceed service profile")
        if not 1 <= self.pids_limit <= 64 or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("worker resource limits exceed service ceiling")


def docker_run_args(profile: WorkerProfile) -> list[str]:
    """Construct a fixed docker invocation; callers cannot supply arbitrary args."""
    return [
        "docker", "run", "--rm", "-i", "--network=none", "--read-only",
        "--user", profile.user, "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--security-opt=apparmor=docker-default",
        "--pids-limit", str(profile.pids_limit), "--memory", profile.memory_limit,
        "--cpus", str(profile.nano_cpus / 1_000_000_000), "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={profile.tmpfs_size}",
        profile.image, *profile.command,
    ]


def profile_from_file(path: Path) -> WorkerProfile:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("network") != "none" or document.get("host_mounts") != []:
        raise ValueError("worker profile must disable network and host mounts")
    if (document.get("capabilities") != "drop-all" or document.get("read_only_root") is not True
            or document.get("apparmor") != "docker-default"
            or document.get("seccomp") != "Docker Engine built-in default allowlist"):
        raise ValueError("worker profile security controls mismatch")
    versions = document.get("protocol_versions", ["1.0"])
    if not isinstance(versions, list) or not versions:
        raise ValueError("worker profile must declare supported protocol versions")
    return WorkerProfile(
        image=document["worker_image"],
        user=document["user"],
        read_only=document["read_only_root"],
        pids_limit=document["pids_limit"],
        memory_limit=document["memory"],
        nano_cpus=int(float(document["cpus"]) * 1_000_000_000),
        tmpfs_size=document["tmpfs"]["/tmp"].split(",")[0],
        timeout_seconds=document["timeout_seconds"],
        protocol_versions=tuple(versions),
    )
