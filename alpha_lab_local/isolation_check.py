"""Container-side live readiness command used by the WSL launcher."""

from __future__ import annotations

import json
import os
import sys

from alpha_lab_service.worker_profile import profile_from_file
from alpha_lab_local.config import RuntimeConfig
from alpha_lab_local.isolation import LocalIsolationVerifierV2


def main() -> int:
    config_path = os.environ.get("ALPHA_LAB_LOCAL_CONFIG", "")
    if not config_path:
        print(json.dumps({"verified": False, "error": "FACTORY_CONFIG_UNAVAILABLE"}))
        return 2
    try:
        config = RuntimeConfig.from_file(config_path)
        profile = profile_from_file(config.worker_profile_path)
        report = LocalIsolationVerifierV2(config, profile).verify(worker_image_digest=profile.image)
    except Exception as exc:
        print(json.dumps({"verified": False, "error": type(exc).__name__}))
        return 2
    verified = report.permits(worker_image_digest=profile.image)
    print(json.dumps({
        "verified": verified,
        "worker_image_digest": report.worker_image_digest,
        "checks": dict(report.checks),
    }, sort_keys=True))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
