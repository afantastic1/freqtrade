from pathlib import Path

from alpha_lab_service.manifest import verify_manifest
from alpha_lab_service.governance import verify_v11_core

ROOT = Path(__file__).resolve().parents[2]


def test_owner_boundary_covers_service_worker_gate_workflows_and_manifests():
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    required = (
        "/alpha_lab_service/", "/containers/alpha-lab-factor-worker/",
        "/scripts/alpha_lab_admission_gate.py", "/.github/workflows/alpha-lab-freeze.yml",
        "/.github/workflows/alpha-lab-python-service.yml", "/alpha_lab/freeze-manifest-v1.3.json",
        "/alpha_lab_service/protocol-manifest-v2.json", "/alpha_lab_contracts/",
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
        ".github/workflows/alpha-lab-python-service.yml", "alpha_lab/freeze-manifest-v1.3.json",
        "alpha_lab_service/protocol-manifest-v2.json", "alpha_lab_contracts/**",
        "docs/alpha_lab/python-factor-*.md",
    ):
        assert path in text
    assert "test_worker_container.py" in text
    assert "alpha_lab_service.governance" in text
    assert "ALPHA_LAB_WORKER_IMAGE" in text


def test_new_service_manifest_covers_its_verifier_worker_and_admission_gate():
    manifest = ROOT / "alpha_lab_service/protocol-manifest-v2.json"
    assert verify_manifest(ROOT, manifest, manifest_version="2.0-python-factor-service") == []
    expected = {
        "alpha_lab_service/manifest.py",
        "alpha_lab_service/governance.py",
        "containers/alpha-lab-factor-worker/worker.py",
        "scripts/alpha_lab_admission_gate.py",
        "docs/alpha_lab/python-factor-execution-protocol-v1.md",
        "alpha_lab_contracts/research_request_v2.py",
    }
    import json
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert expected <= set(files)


def test_v12_governance_keeps_every_v11_frozen_core_hash_valid():
    assert verify_v11_core() == []


def test_alpha_lab_contracts_are_covered_by_ci_and_codeowners():
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    assert "/alpha_lab_contracts/" in owners
    assert "/alpha_lab_service/protocol-manifest-v2.json" in owners
    assert "/alpha_lab/freeze-manifest-v1.3.json" in owners

    import json
    protected = json.loads((ROOT / "alpha_lab_service/protected-files.json").read_text(encoding="utf-8"))
    assert "/alpha_lab_contracts/" in protected["required_owner_paths"]
    assert "alpha_lab_contracts/**" in protected["required_ci_paths"]
    assert "/alpha_lab_service/protocol-manifest-v2.json" in protected["required_owner_paths"]

    freeze_text = (ROOT / ".github/workflows/alpha-lab-freeze.yml").read_text(encoding="utf-8")
    service_text = (ROOT / ".github/workflows/alpha-lab-python-service.yml").read_text(encoding="utf-8")
    assert "alpha_lab_contracts/**" in freeze_text and "alpha_lab_contracts/tests/" in freeze_text
    assert "alpha_lab_contracts/**" in service_text


def test_active_governance_selects_v13_and_service_v2():
    import json
    from alpha_lab_service import governance

    assert governance.V13_GOVERNANCE.name == "freeze-manifest-v1.3.json"
    assert governance.SERVICE_MANIFEST.name == "protocol-manifest-v2.json"
    manifest = json.loads(governance.SERVICE_MANIFEST.read_text(encoding="utf-8"))
    assert "alpha_lab_contracts/research_request_v2.py" in manifest["files"]
