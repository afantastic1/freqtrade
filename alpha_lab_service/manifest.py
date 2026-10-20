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
GOVERNANCE_FILES = (
    ".github/CODEOWNERS",
    ".github/workflows/alpha-lab-freeze.yml",
    ".github/workflows/alpha-lab-python-service.yml",
    "scripts/alpha_lab_admission_gate.py",
)
EXCLUDED_MANIFESTS = {
    "alpha_lab_service/protocol-manifest-v1.json",
    "alpha_lab/freeze-manifest-v1.2.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths(root: Path, governance: bool) -> list[Path]:
    patterns = GOVERNANCE_FILES if governance else SERVICE_FILES
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


def build_manifest(root: Path, *, governance: bool = False) -> dict:
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in _paths(root, governance)
        if path.relative_to(root).as_posix() not in EXCLUDED_MANIFESTS
    }
    return {
        "manifest_version": "1.2-governance" if governance else "1.0-python-factor-service",
        "kind": "governance-boundary" if governance else "python-factor-service",
        "hash": "sha256",
        "files": files,
    }


def verify_manifest(root: Path, manifest_path: Path, *, governance: bool = False) -> list[str]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest: {exc}"]
    expected = document.get("files")
    if not isinstance(expected, dict):
        return ["manifest: files mapping missing"]
    current = build_manifest(root, governance=governance)["files"]
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
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="alpha_lab_service/protocol-manifest-v1.json")
    parser.add_argument("--governance", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / args.manifest
    if args.write:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(build_manifest(root, governance=args.governance), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    errors = verify_manifest(root, manifest_path, governance=args.governance)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
