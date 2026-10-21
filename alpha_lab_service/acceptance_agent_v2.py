"""Build provenance-rich Agent gateway acceptance reports from measured evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_ISOLATION_CHECKS = frozenset({
    "worker_image_immutable", "worker_os_identity_separated", "network_disabled",
    "docker_socket_unavailable", "read_only_root_filesystem", "data_mount_read_only",
    "stores_write_restricted", "resource_limits_enforced",
})


def build_acceptance_report(
    *,
    code_head: str,
    catalog_evidence: dict[str, Any],
    isolation_evidence: dict[str, Any],
    campaign_sha256: str,
    request_sha256: str | None,
    tests: dict[str, str],
    dispatch_status: str = "NOT_DISPATCHED",
    evidence_id: str | None = None,
) -> dict[str, Any]:
    """Classify independent real-data and isolation gates without performing I/O."""
    if not isinstance(code_head, str) or not _HEAD.fullmatch(code_head):
        raise ValueError("code_head must be a full lowercase commit SHA")
    if not isinstance(campaign_sha256, str) or not _SHA256.fullmatch(campaign_sha256):
        raise ValueError("campaign_sha256 must be a lowercase SHA-256")
    if request_sha256 is not None and not _SHA256.fullmatch(request_sha256):
        raise ValueError("request_sha256 must be a lowercase SHA-256 or null")
    if not isinstance(catalog_evidence, dict) or not isinstance(isolation_evidence, dict):
        raise ValueError("catalog and isolation evidence must be objects")
    if not isinstance(tests, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tests.items()):
        raise ValueError("tests must map names to result strings")

    catalog_status = catalog_evidence.get("status")
    observed_count = catalog_evidence.get("observed_count", 0)
    catalog_hashes_valid = (
        isinstance(catalog_evidence.get("manifest_sha256"), str)
        and _SHA256.fullmatch(catalog_evidence["manifest_sha256"]) is not None
        and isinstance(catalog_evidence.get("partition_sha256"), list)
        and bool(catalog_evidence["partition_sha256"])
        and all(isinstance(value, str) and _SHA256.fullmatch(value) for value in catalog_evidence["partition_sha256"])
    )
    real_data_passed = catalog_status == "AVAILABLE" and isinstance(observed_count, int) and observed_count > 0 and catalog_hashes_valid
    checks = isolation_evidence.get("checks")
    isolation_passed = (
        isolation_evidence.get("verified") is True
        and isinstance(checks, dict)
        and _REQUIRED_ISOLATION_CHECKS <= checks.keys()
        and all(checks[name] == "passed" for name in _REQUIRED_ISOLATION_CHECKS)
    )
    gates = {
        "real_data": "PASSED" if real_data_passed else "BLOCKED_DATA_UNAVAILABLE",
        "isolation": "PASSED" if isolation_passed else "BLOCKED_ISOLATION_UNVERIFIED",
    }
    dispatch_allowed = real_data_passed and isolation_passed
    if not dispatch_allowed:
        dispatch_status = "NOT_DISPATCHED"
        evidence_id = None
    return {
        "protocol": "alpha-lab-agent-gateway-acceptance-v1",
        "code_head": code_head,
        "campaign_sha256": campaign_sha256,
        "request_sha256": request_sha256,
        "catalog": {
            "status": catalog_status,
            "observed_count": observed_count,
            "manifest_sha256": catalog_evidence.get("manifest_sha256"),
            "partition_sha256": catalog_evidence.get("partition_sha256", []),
            "requested_interval": catalog_evidence.get("requested_interval"),
            "snapshot_id": catalog_evidence.get("snapshot_id"),
        },
        "isolation": {
            "verified": isolation_passed,
            "checks": {name: checks.get(name) for name in sorted(_REQUIRED_ISOLATION_CHECKS)} if isinstance(checks, dict) else {},
            "proof_digest": isolation_evidence.get("proof_digest"),
        },
        "synthetic_checks": {key: value for key, value in tests.items() if "synthetic" in key or "gateway" in key},
        "real_data_tests": {key: value for key, value in tests.items() if "real" in key or "e2e" in key},
        "test_results": dict(tests),
        "gates": gates,
        "dispatch_status": dispatch_status,
        "evidence_id": evidence_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, help="operator-generated evidence JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    report = build_acceptance_report(**payload)
    Path(args.output).write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report["gates"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
