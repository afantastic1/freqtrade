"""Live, fail-closed probes for the Docker-hosted local MCP service."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat

from alpha_lab_service.isolation_v2 import IsolationReadinessV2, REQUIRED_ISOLATION_CHECKS_V2
from alpha_lab_service.worker_profile import WorkerProfile, docker_run_args


_WORKER_UID = 65532
_SERVICE_MEMORY_MAX = 1024 * 1024 * 1024


def _mount_entries(mountinfo: str) -> list[tuple[str, set[str], set[str]]]:
    entries = []
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        mountpoint = fields[4].replace("\\040", " ").replace("\\011", "\t")
        mount_options = set(fields[5].split(","))
        super_options = set(fields[separator + 3].split(","))
        entries.append((mountpoint, mount_options, super_options))
    return entries


def _read_only_mount(path: Path, entries) -> bool:
    target = str(path.resolve(strict=True))
    matches = [entry for entry in entries
               if target == entry[0] or target.startswith(entry[0].rstrip("/") + "/")]
    if not matches:
        return False
    mountpoint, mount_options, super_options = max(matches, key=lambda item: len(item[0]))
    return "ro" in mount_options or "ro" in super_options


def _limited_cgroup(cgroup_root: Path) -> bool:
    try:
        memory = int((cgroup_root / "memory.max").read_text(encoding="ascii").strip())
        pids = int((cgroup_root / "pids.max").read_text(encoding="ascii").strip())
        quota, period = (int(item) for item in (cgroup_root / "cpu.max").read_text(encoding="ascii").split())
    except (OSError, ValueError):
        return False
    return 0 < memory <= _SERVICE_MEMORY_MAX and 0 < pids <= 64 and 0 < quota <= period


def _capabilities_dropped(status_path: Path) -> bool:
    try:
        fields = dict(
            line.split(":", 1) for line in status_path.read_text(encoding="ascii").splitlines()
            if ":" in line
        )
        return int(fields.get("CapEff", "").strip(), 16) == 0
    except (OSError, ValueError):
        return False


class LocalIsolationVerifierV2:
    """Observe the MCP container and pinned Worker profile on every execution."""

    def __init__(self, config, worker_profile: WorkerProfile):
        self.config = config
        self.worker_profile = worker_profile

    def verify(self, *, worker_image_digest: str) -> IsolationReadinessV2:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            mountinfo = ""
        mounts = _mount_entries(mountinfo)
        root_readonly = any(
            point == "/" and ("ro" in options or "ro" in super_options)
            for point, options, super_options in mounts
        )
        socket_paths = (Path("/var/run/docker.sock"), Path("/run/docker.sock"))
        socket_unavailable = not any(path.exists() for path in socket_paths) and not any(
            point in {"/var/run/docker.sock", "/run/docker.sock"} for point, _, _ in mounts
        )
        try:
            data_readonly = bool(mounts) and all(
                _read_only_mount(path, mounts)
                for path in (self.config.catalog_root, *self.config.source_roots.values())
            )
        except OSError:
            data_readonly = False
        state_paths = (
            self.config.research_store_path.parent,
            self.config.dataset_output_root,
        )
        state_restricted = True
        for path in state_paths:
            try:
                info = path.stat()
                state_restricted = state_restricted and (
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) & 0o077 == 0
                    and os.access(path, os.W_OK)
                    and not path.is_symlink()
                )
            except OSError:
                state_restricted = False
        run_args = docker_run_args(self.worker_profile)
        required_args = {
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
        }
        args_secure = required_args.issubset(set(run_args))
        try:
            args_secure = args_secure and self.worker_profile.pids_limit <= 64
            args_secure = args_secure and self.worker_profile.memory_limit == "512m"
            args_secure = args_secure and 0 < self.worker_profile.nano_cpus <= 1_000_000_000
            args_secure = args_secure and 0 < self.worker_profile.timeout_seconds <= 300
        except (AttributeError, TypeError):
            args_secure = False
        try:
            cgroup_limited = _limited_cgroup(Path("/sys/fs/cgroup"))
        except Exception:
            cgroup_limited = False
        checks = {
            "worker_image_immutable": (
                worker_image_digest == self.worker_profile.image
                and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", worker_image_digest))
            ),
            "worker_os_identity_separated": (
                os.geteuid() != 0 and os.geteuid() != _WORKER_UID
                and self.worker_profile.user.split(":", 1)[0] != str(os.geteuid())
                and _capabilities_dropped(Path("/proc/self/status"))
            ),
            "network_disabled": self.worker_profile.network == "none" and args_secure,
            "docker_socket_unavailable": socket_unavailable,
            "read_only_root_filesystem": root_readonly,
            "data_mount_read_only": data_readonly,
            "stores_write_restricted": state_restricted,
            "resource_limits_enforced": args_secure and cgroup_limited,
        }
        # Keep the report shape fixed; omitted checks fail readiness.
        checks = {name: checks.get(name) is True for name in REQUIRED_ISOLATION_CHECKS_V2}
        return IsolationReadinessV2(worker_image_digest, datetime.now(timezone.utc), checks)
