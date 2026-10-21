"""Verify Docker's live service-container configuration without printing secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from alpha_lab_local.config import DATASET_ID


def _discovery_partitions_only(normalized: Path) -> bool:
    dataset_root = normalized / DATASET_ID
    try:
        years = {item.name for item in dataset_root.iterdir() if item.is_dir()}
    except OSError:
        return False
    return years == {"year=2022", "year=2023", "year=2024"}


def _inspect(container: str) -> dict:
    result = subprocess.run(
        ["docker", "inspect", container], check=True, capture_output=True, text=True
    )
    document = json.loads(result.stdout)
    if len(document) != 1:
        raise ValueError("expected exactly one MCP service container")
    return document[0]


def verify_container(container: str, *, catalog: Path, normalized: Path, state: Path,
                     worker_image: str, uid: int, gid: int) -> dict:
    docker_access = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    details = _inspect(container)
    host = details["HostConfig"]
    mounts = {item["Destination"]: item for item in details.get("Mounts", [])}
    profile_image = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", worker_image],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    env = details["Config"].get("Env", [])
    checks = {
        "broker_host_has_docker_permission": bool(docker_access),
        "service_container_running": details.get("State", {}).get("Running") is True,
        "service_user_matches_host_owner": details["Config"].get("User") == f"{uid}:{gid}",
        "host_loopback_network_for_broker": host.get("NetworkMode") == "host",
        "read_only_root_filesystem": host.get("ReadonlyRootfs") is True,
        "no_docker_socket_mount": not any(
            "docker.sock" in item.get("Source", "") or "docker.sock" in item.get("Destination", "")
            for item in details.get("Mounts", [])
        ) and not any(item.startswith("DOCKER_HOST=") for item in env),
        "only_approved_mounts_present": set(mounts) == {
            "/data/catalog", "/data/normalized", "/var/lib/alpha-lab"
        },
        "catalog_mount_read_only": (
            mounts.get("/data/catalog", {}).get("RW") is False
            and Path(mounts.get("/data/catalog", {}).get("Source", "/")).resolve() == catalog.resolve()
        ),
        "normalized_data_mount_read_only": (
            mounts.get("/data/normalized", {}).get("RW") is False
            and Path(mounts.get("/data/normalized", {}).get("Source", "/")).resolve() == normalized.resolve()
        ),
        "state_mount_private": (
            mounts.get("/var/lib/alpha-lab", {}).get("RW") is True
            and Path(mounts.get("/var/lib/alpha-lab", {}).get("Source", "/")).resolve() == state.resolve()
            and (state.stat().st_mode & 0o077) == 0
        ),
        "container_capabilities_dropped": "ALL" in (host.get("CapDrop") or []),
        "no_new_privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
        "service_resource_limits_enforced": (
            0 < host.get("Memory", 0) <= 1024 * 1024 * 1024
            and 0 < host.get("NanoCpus", 0) <= 1_000_000_000
            and 0 < host.get("PidsLimit", 0) <= 64
        ),
        "worker_image_digest_matches_profile": profile_image == worker_image,
        "dataset_is_expected_btc_catalog": (
            (catalog / "catalog-v3" / "datasets" / DATASET_ID).is_dir()
        ),
        "discovery_source_contains_only_2022_2024": _discovery_partitions_only(normalized),
    }
    return {"verified": all(checks.values()), "checks": checks}


def main() -> int:
    try:
        result = verify_container(
            os.environ["ALPHA_LAB_MCP_CONTAINER"],
            catalog=Path(os.environ["ALPHA_LAB_CATALOG_HOST"]),
            normalized=Path(os.environ["ALPHA_LAB_NORMALIZED_HOST"]),
            state=Path(os.environ["ALPHA_LAB_STATE_HOST"]),
            worker_image=os.environ["ALPHA_LAB_WORKER_IMAGE"],
            uid=os.getuid(), gid=os.getgid(),
        )
    except Exception as exc:
        print(json.dumps({"verified": False, "error": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
