from pathlib import Path

from alpha_lab_service.manifest import verify_manifest
from alpha_lab_service.governance import verify_v11_core

ROOT = Path(__file__).resolve().parents[2]


def test_owner_boundary_covers_service_worker_gate_workflows_and_manifests():
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    required = (
        "/alpha_lab_service/", "/containers/alpha-lab-factor-worker/",
        "/scripts/alpha_lab_admission_gate.py", "/.github/workflows/alpha-lab-freeze.yml",
        "/.github/workflows/alpha-lab-python-service.yml", "/alpha_lab/freeze-manifest-v1.2.json",
        "/docs/alpha_lab/",
    )
    assert all(path in owners for path in required)


def test_ci_triggers_and_checks_all_service_security_boundaries():
    workflow = ROOT / ".github/workflows/alpha-lab-python-service.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    for path in (
        "alpha_lab_service/**", "containers/alpha-lab-factor-worker/**",
        "scripts/alpha_lab_admission_gate.py", ".github/CODEOWNERS",
        ".github/workflows/alpha-lab-python-service.yml", "alpha_lab/freeze-manifest-v1.2.json",
        "docs/alpha_lab/python-factor-*.md",
    ):
        assert path in text
    assert "test_worker_container.py" in text
    assert "alpha_lab_service.governance" in text
    assert "ALPHA_LAB_WORKER_IMAGE" in text


def test_new_service_manifest_covers_its_verifier_worker_and_admission_gate():
    manifest = ROOT / "alpha_lab_service/protocol-manifest-v1.json"
    assert verify_manifest(ROOT, manifest) == []
    expected = {
        "alpha_lab_service/manifest.py",
        "alpha_lab_service/governance.py",
        "containers/alpha-lab-factor-worker/worker.py",
        "scripts/alpha_lab_admission_gate.py",
        "docs/alpha_lab/python-factor-execution-protocol-v1.md",
    }
    import json
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert expected <= set(files)


def test_v12_governance_keeps_every_v11_frozen_core_hash_valid():
    assert verify_v11_core() == []
