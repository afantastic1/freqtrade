"""Fail-closed validation for trusted Agent execution-readiness reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Protocol

from alpha_lab_contracts._timestamps import parse_rfc3339_utc


REQUIRED_ISOLATION_CHECKS_V2 = frozenset({
    "worker_image_immutable",
    "worker_os_identity_separated",
    "network_disabled",
    "docker_socket_unavailable",
    "read_only_root_filesystem",
    "data_mount_read_only",
    "stores_write_restricted",
    "resource_limits_enforced",
})
_IMAGE_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|.+@sha256:[0-9a-f]{64})$")
_MAX_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class IsolationReadinessV2:
    """Structured report produced only by an operator-configured verifier."""

    worker_image_digest: str
    verified_at: datetime
    checks: Mapping[str, bool]

    def permits(self, *, worker_image_digest: str, now: datetime | None = None) -> bool:
        if not isinstance(worker_image_digest, str) or not _IMAGE_DIGEST.fullmatch(worker_image_digest):
            return False
        if self.worker_image_digest != worker_image_digest or not _IMAGE_DIGEST.fullmatch(self.worker_image_digest):
            return False
        if not isinstance(self.verified_at, datetime) or self.verified_at.tzinfo is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return False
        checked = self.verified_at.astimezone(timezone.utc)
        current = current.astimezone(timezone.utc)
        if checked > current or current - checked > _MAX_AGE:
            return False
        if not isinstance(self.checks, Mapping):
            return False
        return all(self.checks.get(name) is True for name in REQUIRED_ISOLATION_CHECKS_V2)

    @classmethod
    def from_value(cls, value: object) -> "IsolationReadinessV2 | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or set(value) != {
            "worker_image_digest", "verified_at", "checks"
        }:
            return None
        image = value["worker_image_digest"]
        checks = value["checks"]
        if not isinstance(image, str) or not isinstance(checks, Mapping):
            return None
        try:
            verified_at = (
                value["verified_at"] if isinstance(value["verified_at"], datetime)
                else parse_rfc3339_utc(value["verified_at"], "verified_at")
            )
        except (TypeError, ValueError):
            return None
        return cls(image, verified_at, dict(checks))


class IsolationVerifierV2(Protocol):
    """Trusted operator-side verifier; it is never selected by Agent input."""

    def verify(self, *, worker_image_digest: str) -> IsolationReadinessV2 | Mapping[str, object]: ...


def verify_agent_execution_readiness(
    verifier: IsolationVerifierV2 | None,
    *,
    worker_image_digest: str,
    now: datetime | None = None,
) -> bool:
    """Return false for absent, stale, malformed, mismatched, or failed proof."""
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        return False
    try:
        report = IsolationReadinessV2.from_value(
            verifier.verify(worker_image_digest=worker_image_digest)
        )
    except Exception:
        return False
    return report is not None and report.permits(worker_image_digest=worker_image_digest, now=now)
