import hashlib
import json
from pathlib import Path

from alpha_lab_service.manifest import build_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[2]
V11_SHA256 = "692eda36c07f9dfd113dc57d470f7db15507fc75321bc5a79bf5edf9ff9561c2"


def test_service_manifest_detects_modified_and_untracked_protected_files(tmp_path):
    (tmp_path / "alpha_lab_service").mkdir()
    (tmp_path / "alpha_lab_service/protocol.py").write_text("protocol\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/alpha_lab_admission_gate.py").write_text("gate\n")
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/alpha-lab-python-service.yml").write_text("workflow\n")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(build_manifest(tmp_path)))
    assert verify_manifest(tmp_path, manifest_path) == []

    (tmp_path / "alpha_lab_service/protocol.py").write_text("changed\n")
    errors = verify_manifest(tmp_path, manifest_path)
    assert "changed: alpha_lab_service/protocol.py" in errors

    (tmp_path / "alpha_lab_service/worker_extra.py").write_text("untracked\n")
    errors = verify_manifest(tmp_path, manifest_path)
    assert any("untracked protected file" in error for error in errors)


def test_governance_manifest_covers_codeowners_and_active_workflow(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/CODEOWNERS").write_text("* @maintainer\n")
    (tmp_path / ".github/workflows").mkdir()
    (tmp_path / ".github/workflows/alpha-lab-freeze.yml").write_text("workflow\n")
    path = tmp_path / "governance.json"
    path.write_text(json.dumps(build_manifest(tmp_path, governance=True)))
    assert verify_manifest(tmp_path, path, governance=True) == []
    (tmp_path / ".github/CODEOWNERS").write_text("* @other\n")
    assert "changed: .github/CODEOWNERS" in verify_manifest(tmp_path, path, governance=True)


def test_v11_manifest_remains_byte_identical():
    digest = hashlib.sha256((ROOT / "alpha_lab/freeze-manifest-v1.1.json").read_bytes()).hexdigest()
    assert digest == V11_SHA256
