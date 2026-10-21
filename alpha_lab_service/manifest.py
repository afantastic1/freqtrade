"""Independent service and governance boundary manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SERVICE_FILES = (
    "alpha_lab_service/",
    "containers/alpha-lab-factor-worker/",
    ".github/workflows/alpha-lab-python-service.yml",
    "docs/alpha_lab/python-factor-execution-protocol-v1.md",
    "docs/alpha_lab/python-factor-service-acceptance.md",
    "scripts/alpha_lab_admission_gate.py",
    ".github/CODEOWNERS",
)
SERVICE_V2_FILES = SERVICE_FILES + ("alpha_lab_contracts/",)
SERVICE_V3_FILES = SERVICE_V2_FILES + (
    "alpha_lab/", "docs/alpha_lab/dynamic_dataset_builder_acceptance.md",
    "docs/alpha_lab/python-factor-execution-protocol-v2.md",
    "docs/alpha_lab/python-factor-service-v2-acceptance.md",
    "docs/alpha_lab/python-factor-service-v2-acceptance.json",
    "docs/alpha_lab/unified-descriptive-evidence-acceptance.md",
    "docs/alpha_lab/unified-descriptive-evidence-acceptance.json",
)
GOVERNANCE_FILES = (
    ".github/CODEOWNERS",
    ".github/workflows/alpha-lab-freeze.yml",
    ".github/workflows/alpha-lab-python-service.yml",
    "scripts/alpha_lab_admission_gate.py",
)
GOVERNANCE_V13_FILES = GOVERNANCE_FILES + (
    "alpha_lab_service/manifest.py",
    "alpha_lab_service/governance.py",
    "alpha_lab_service/protected-files.json",
)
GOVERNANCE_V14_FILES = GOVERNANCE_V13_FILES + ("alpha_lab/freeze-manifest-core-v1.2.json",)
EXCLUDED_SERVICE_MANIFESTS = {
    "alpha_lab_service/protocol-manifest-v1.json",
    "alpha_lab_service/protocol-manifest-v2.json",
    "alpha_lab_service/protocol-manifest-v3.json",
}
EXCLUDED_GOVERNANCE_MANIFESTS = {
    "alpha_lab/freeze-manifest-v1.2.json",
    "alpha_lab/freeze-manifest-v1.3.json",
    "alpha_lab/freeze-manifest-v1.4.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths(root: Path, governance: bool, manifest_version: str) -> list[Path]:
    if governance:
        patterns = (GOVERNANCE_V14_FILES if manifest_version == "1.4-governance" else
                    GOVERNANCE_V13_FILES if manifest_version == "1.3-governance" else GOVERNANCE_FILES)
    else:
        patterns = (SERVICE_V3_FILES if manifest_version == "3.0-python-factor-service" else
                    SERVICE_V2_FILES if manifest_version == "2.0-python-factor-service" else SERVICE_FILES)
    result: set[Path] = set()
    for item in patterns:
        candidate = root / item
        if item.endswith("/"):
            if candidate.exists():
                result.update(
                    path for path in candidate.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                )
        elif candidate.is_file():
            result.add(candidate)
    return sorted(result)


def build_manifest(root: Path, *, governance: bool = False, manifest_version: str | None = None) -> dict:
    defaults = {True: "1.2-governance", False: "1.0-python-factor-service"}
    manifest_version = manifest_version or defaults[governance]
    supported = {"1.2-governance", "1.3-governance", "1.4-governance"} if governance else {"1.0-python-factor-service", "2.0-python-factor-service", "3.0-python-factor-service"}
    if manifest_version not in supported:
        raise ValueError(f"unsupported {'governance' if governance else 'service'} manifest version: {manifest_version}")
    excluded = EXCLUDED_GOVERNANCE_MANIFESTS if governance else EXCLUDED_SERVICE_MANIFESTS
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in _paths(root, governance, manifest_version)
        if path.relative_to(root).as_posix() not in excluded
    }
    return {
        "manifest_version": manifest_version,
        "kind": "governance-boundary" if governance else "python-factor-service",
        "hash": "sha256",
        "files": files,
    }


def verify_manifest(root: Path, manifest_path: Path, *, governance: bool = False, manifest_version: str | None = None) -> list[str]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest: {exc}"]
    expected = document.get("files")
    if not isinstance(expected, dict):
        return ["manifest: files mapping missing"]
    manifest_version = manifest_version or document.get("manifest_version")
    try:
        current_manifest = build_manifest(root, governance=governance, manifest_version=manifest_version)
    except ValueError as exc:
        return [f"metadata: {exc}"]
    current = current_manifest["files"]
    errors: list[str] = []
    for relative, wanted in expected.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
        elif _sha256(path) != wanted:
            errors.append(f"changed: {relative}")
    for relative in sorted(set(current) - set(expected)):
        errors.append(f"untracked protected file: {relative}")
    expected_kind = "governance-boundary" if governance else "python-factor-service"
    if document.get("kind") != expected_kind:
        errors.append("metadata: kind")
    if document.get("manifest_version") != manifest_version:
        errors.append("metadata: manifest_version")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="alpha_lab_service/protocol-manifest-v1.json")
    parser.add_argument("--governance", action="store_true")
    parser.add_argument("--manifest-version")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / args.manifest
    if args.write:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(build_manifest(root, governance=args.governance, manifest_version=args.manifest_version), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    errors = verify_manifest(root, manifest_path, governance=args.governance, manifest_version=args.manifest_version)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
