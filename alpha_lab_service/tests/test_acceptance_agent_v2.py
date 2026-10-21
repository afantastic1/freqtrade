from __future__ import annotations

import socket

from alpha_lab_service.acceptance_agent_v2 import build_acceptance_report


def test_acceptance_distinguishes_synthetic_checks_from_real_data_and_has_no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("acceptance report must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    report = build_acceptance_report(
        code_head="a" * 40,
        catalog_evidence={"status": "MISSING", "observed_count": 0, "manifest_sha256": None,
                          "partition_sha256": []},
        isolation_evidence={"verified": False, "checks": {}},
        campaign_sha256="b" * 64,
        request_sha256=None,
        tests={"synthetic_gateway": "passed", "real_data_e2e": "not_run"},
    )

    assert report["synthetic_checks"]["synthetic_gateway"] == "passed"
    assert report["gates"]["real_data"] == "BLOCKED_DATA_UNAVAILABLE"
    assert report["gates"]["isolation"] == "BLOCKED_ISOLATION_UNVERIFIED"
    assert report["dispatch_status"] == "NOT_DISPATCHED"
    assert report["evidence_id"] is None
    assert report["catalog"]["status"] == "MISSING"


def test_acceptance_requires_complete_real_catalog_and_isolation_for_e2e_pass():
    report = build_acceptance_report(
        code_head="a" * 40,
        catalog_evidence={"status": "AVAILABLE", "observed_count": 1,
                          "manifest_sha256": "c" * 64, "partition_sha256": ["d" * 64]},
        isolation_evidence={"verified": True, "checks": {
            "worker_image_immutable": "passed", "worker_os_identity_separated": "passed",
            "network_disabled": "passed", "docker_socket_unavailable": "passed",
            "read_only_root_filesystem": "passed", "data_mount_read_only": "passed",
            "stores_write_restricted": "passed", "resource_limits_enforced": "passed",
        }},
        campaign_sha256="b" * 64,
        request_sha256="e" * 64,
        tests={"synthetic_gateway": "passed", "real_data_e2e": "passed"},
        dispatch_status="COMPLETED",
        evidence_id="evidence-123",
    )
    assert report["gates"] == {"real_data": "PASSED", "isolation": "PASSED"}
    assert report["dispatch_status"] == "COMPLETED"
    assert report["evidence_id"] == "evidence-123"


def test_incomplete_isolation_claim_does_not_pass_gate():
    report = build_acceptance_report(
        code_head="a" * 40,
        catalog_evidence={"status": "AVAILABLE", "observed_count": 1,
                          "manifest_sha256": "c" * 64, "partition_sha256": ["d" * 64]},
        isolation_evidence={"verified": True, "checks": {"seccomp": "passed"}},
        campaign_sha256="b" * 64,
        request_sha256=None,
        tests={},
    )
    assert report["gates"]["isolation"] == "BLOCKED_ISOLATION_UNVERIFIED"
    assert report["dispatch_status"] == "NOT_DISPATCHED"
