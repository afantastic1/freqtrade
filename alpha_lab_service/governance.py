"""Active v1.8 integrity gate: historical and active core plus current governance and service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpha_lab.freeze import build_manifest
from alpha_lab.freeze import verify_manifest as verify_core_manifest

from alpha_lab_service.manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
V11_MANIFEST = ROOT / "alpha_lab/freeze-manifest-v1.1.json"
CORE_V15_MANIFEST = ROOT / "alpha_lab/freeze-manifest-core-v1.5.json"
V18_GOVERNANCE = ROOT / "alpha_lab/freeze-manifest-v1.8.json"
SERVICE_MANIFEST = ROOT / "alpha_lab_service/protocol-manifest-v7.json"
REPLACED_BOUNDARY = {
    ".github/CODEOWNERS",
    ".github/workflows/alpha-lab-freeze.yml",
    # Dependency-only changes are locked by the new active core manifest.
    "alpha_lab/data/market_panel.py",
    "alpha_lab/data/quality.py",
    "alpha_lab/data/loader.py",
    "alpha_lab/data/availability.py",
    "alpha_lab/data/dataset_builder.py",
    "alpha_lab/data/pit_labels.py",
    "alpha_lab/data/temporal_plan.py",
    "alpha_lab/baseline_cli.py",
    "alpha_lab/non_ohlcv_cli.py",
    "alpha_lab/regime_cli.py",
    "alpha_lab/runner.py",
    "alpha_lab/evaluation/cross_sectional.py",
    "alpha_lab/factors/momentum.py",
    "alpha_lab/labels/cross_sectional.py",
    "alpha_lab/labels/forward_returns.py",
}


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
    errors.extend(f"active core v1.5 {item}" for item in verify_core_manifest(ROOT, CORE_V15_MANIFEST))
    errors.extend(f"v1.8 governance {item}" for item in verify_manifest(ROOT, V18_GOVERNANCE, governance=True, manifest_version="1.8-governance"))
    errors.extend(f"Python service v7 {item}" for item in verify_manifest(ROOT, SERVICE_MANIFEST, manifest_version="7.0-python-factor-service"))
    return errors


def main() -> int:
    errors = verify_all()
    for error in errors:
        print(error)
    if not errors:
        print("Alpha Lab v1.8 governance, service v7, active core v1.5, and historical core v1.1: valid")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
