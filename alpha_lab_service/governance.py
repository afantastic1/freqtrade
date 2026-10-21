"""Active v1.6 integrity gate: historical and active core plus current governance and service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpha_lab.freeze import build_manifest
from alpha_lab.freeze import verify_manifest as verify_core_manifest

from alpha_lab_service.manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
V11_MANIFEST = ROOT / "alpha_lab/freeze-manifest-v1.1.json"
CORE_V13_MANIFEST = ROOT / "alpha_lab/freeze-manifest-core-v1.3.json"
V16_GOVERNANCE = ROOT / "alpha_lab/freeze-manifest-v1.6.json"
SERVICE_MANIFEST = ROOT / "alpha_lab_service/protocol-manifest-v5.json"
REPLACED_BOUNDARY = {".github/CODEOWNERS", ".github/workflows/alpha-lab-freeze.yml"}


def verify_v11_core() -> list[str]:
    document = json.loads(V11_MANIFEST.read_text(encoding="utf-8"))
    errors = []
    for relative, wanted in document["files"].items():
        if relative in REPLACED_BOUNDARY:
            continue
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"frozen core missing: {relative}")
            continue
        from alpha_lab.freeze import _sha256
        if _sha256(path) != wanted:
            errors.append(f"frozen core changed: {relative}")
    current_document = build_manifest(ROOT)
    for key in ("engine_version", "protocol_version", "statistical_registry_version"):
        if current_document.get(key) != document.get(key):
            errors.append(f"frozen core metadata changed: {key}")
    return errors


def verify_all() -> list[str]:
    errors = verify_v11_core()
    errors.extend(f"active core v1.3 {item}" for item in verify_core_manifest(ROOT, CORE_V13_MANIFEST))
    errors.extend(f"v1.6 governance {item}" for item in verify_manifest(ROOT, V16_GOVERNANCE, governance=True, manifest_version="1.6-governance"))
    errors.extend(f"Python service v5 {item}" for item in verify_manifest(ROOT, SERVICE_MANIFEST, manifest_version="5.0-python-factor-service"))
    return errors


def main() -> int:
    errors = verify_all()
    for error in errors:
        print(error)
    if not errors:
        print("Alpha Lab v1.6 governance, service v5, active core v1.3, and historical core v1.1: valid")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
