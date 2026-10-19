"""Create and verify the Alpha Lab frozen-core content manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from alpha_lab.version import ENGINE_VERSION, PROTOCOL_VERSION


FROZEN_PREFIXES = (
    "alpha_lab/data/", "alpha_lab/labels/", "alpha_lab/evaluation/",
    "alpha_lab/models/", "alpha_lab/stats/", "alpha_lab/validation/",
    "alpha_lab/factors/", "alpha_lab/tests/",
)
FROZEN_FILES = frozenset({
    "alpha_lab/runner.py", "alpha_lab/research_store.py",
    "alpha_lab/experiment_registry.py", "alpha_lab/config.py",
    "alpha_lab/candidate_cli.py", "alpha_lab/audit_cli.py",
    "alpha_lab/baseline_cli.py", "alpha_lab/regime_cli.py",
    "alpha_lab/non_ohlcv_cli.py", "alpha_lab/execution_cli.py",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, *, engine_version: str = ENGINE_VERSION) -> dict:
    files = {}
    alpha_root = root / "alpha_lab"
    for path in sorted(alpha_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if (
            path.parent == alpha_root
            or relative in FROZEN_FILES
            or relative.startswith(FROZEN_PREFIXES)
        ):
            files[relative] = _sha256(path)
    return {
        "engine_version": engine_version,
        "protocol_version": PROTOCOL_VERSION,
        "hash": "sha256",
        "files": files,
    }


def verify_manifest(root: Path, manifest_path: Path) -> list[str]:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = document["files"]
    errors = []
    if document.get("engine_version") != ENGINE_VERSION:
        errors.append("metadata: engine_version")
    if document.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("metadata: protocol_version")
    for relative, wanted in expected.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
        elif _sha256(path) != wanted:
            errors.append(f"changed: {relative}")
    current = build_manifest(root)["files"]
    for relative in sorted(set(current) - set(expected)):
        errors.append(f"untracked frozen file: {relative}")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="alpha_lab/freeze-manifest.json")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    manifest = root / args.manifest
    if args.write:
        manifest.write_text(
            json.dumps(build_manifest(root), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    errors = verify_manifest(root, manifest)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
