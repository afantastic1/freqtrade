"""Active v1.2 integrity gate: frozen v1.1 core plus separately versioned governance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpha_lab.freeze import build_manifest

from alpha_lab_service.manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
V11_MANIFEST = ROOT / "alpha_lab/freeze-manifest-v1.1.json"
V12_GOVERNANCE = ROOT / "alpha_lab/freeze-manifest-v1.2.json"
SERVICE_MANIFEST = ROOT / "alpha_lab_service/protocol-manifest-v1.json"
REPLACED_BOUNDARY = {".github/CODEOWNERS", ".github/workflows/alpha-lab-freeze.yml"}


def verify_v11_core() -> list[str]:
    document = json.loads(V11_MANIFEST.read_text(encoding="utf-8"))
    expected = {key: value for key, value in document["files"].items() if key not in REPLACED_BOUNDARY}
    current_document = build_manifest(ROOT)
    current = {key: value for key, value in current_document["files"].items() if key not in REPLACED_BOUNDARY}
    errors = []
    for path in sorted(set(expected) - set(current)):
        errors.append(f"frozen core missing: {path}")
    for path in sorted(set(current) - set(expected)):
        errors.append(f"untracked frozen core file: {path}")
    for path in sorted(set(current) & set(expected)):
        if current[path] != expected[path]:
            errors.append(f"frozen core changed: {path}")
    for key in ("engine_version", "protocol_version", "statistical_registry_version"):
        if current_document.get(key) != document.get(key):
            errors.append(f"frozen core metadata changed: {key}")
    return errors


def verify_all() -> list[str]:
    errors = verify_v11_core()
    errors.extend(f"v1.2 governance {item}" for item in verify_manifest(ROOT, V12_GOVERNANCE, governance=True))
    errors.extend(f"Python service {item}" for item in verify_manifest(ROOT, SERVICE_MANIFEST))
    return errors


def main() -> int:
    errors = verify_all()
    for error in errors:
        print(error)
    if not errors:
        print("Alpha Lab v1.2 governance and v1.1 frozen core: valid")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
