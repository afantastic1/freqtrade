import hashlib
import json
import shutil
from pathlib import Path

from alpha_lab_service.manifest import build_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[2]
V11_SHA256 = "692eda36c07f9dfd113dc57d470f7db15507fc75321bc5a79bf5edf9ff9561c2"
V12_SHA256 = "7a0df0e0889bc784d79f5b5b0fd5242105768e2b7b1de103b7eae69d1ea024fd"
SERVICE_V1_SHA256 = "2b9121505500e6e64e653e73a0dc20c465b80731a0276b82cbeb9d8235be3291"


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


def test_service_manifest_detects_tampering_of_real_protected_source(tmp_path):
    manifest_path = ROOT / "alpha_lab_service/protocol-manifest-v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative in document["files"]:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    copied_manifest = tmp_path / "alpha_lab_service/protocol-manifest-v3.json"
    shutil.copyfile(manifest_path, copied_manifest)
    assert verify_manifest(tmp_path, copied_manifest, manifest_version="3.0-python-factor-service") == []

    real_source_copy = tmp_path / "alpha_lab_service/protocol.py"
    real_source_copy.write_bytes(real_source_copy.read_bytes() + b"\n# tampered\n")
    assert "changed: alpha_lab_service/protocol.py" in verify_manifest(
        tmp_path, copied_manifest, manifest_version="3.0-python-factor-service"
    )


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


def test_service_v2_manifest_detects_changed_and_untracked_contract_files(tmp_path):
    contracts = tmp_path / "alpha_lab_contracts"
    contracts.mkdir()
    (contracts / "request.py").write_text("request\n")
    service = tmp_path / "alpha_lab_service"
    service.mkdir()
    (service / "manifest.py").write_text("manifest\n")
    manifest = build_manifest(tmp_path, manifest_version="2.0-python-factor-service")
    assert "alpha_lab_contracts/request.py" in manifest["files"]
    path = tmp_path / "protocol-manifest-v2.json"
    path.write_text(json.dumps(manifest))
    assert verify_manifest(tmp_path, path, manifest_version="2.0-python-factor-service") == []
    (contracts / "request.py").write_text("tampered\n")
    errors = verify_manifest(tmp_path, path, manifest_version="2.0-python-factor-service")
    assert "changed: alpha_lab_contracts/request.py" in errors
    (contracts / "new_schema.json").write_text("{}")
    errors = verify_manifest(tmp_path, path, manifest_version="2.0-python-factor-service")
    assert "untracked protected file: alpha_lab_contracts/new_schema.json" in errors


def test_v13_governance_and_service_v2_preserve_historical_manifest_bytes():
    paths = (
        ROOT / "alpha_lab/freeze-manifest-v1.1.json",
        ROOT / "alpha_lab/freeze-manifest-v1.2.json",
        ROOT / "alpha_lab_service/protocol-manifest-v1.json",
    )
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    assert before == [V11_SHA256, V12_SHA256, SERVICE_V1_SHA256]

    governance = build_manifest(ROOT, governance=True, manifest_version="1.3-governance")
    service = build_manifest(ROOT, manifest_version="2.0-python-factor-service")
    assert governance["manifest_version"] == "1.3-governance"
    assert service["manifest_version"] == "2.0-python-factor-service"
    assert "alpha_lab_contracts/research_request_v2.py" in service["files"]
    after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    assert after == before


def test_v11_core_hashes_remain_unchanged_with_v12_core():
    historical = json.loads((ROOT / "alpha_lab/freeze-manifest-v1.1.json").read_text())
    active = json.loads((ROOT / "alpha_lab/freeze-manifest-core-v1.2.json").read_text())
    replaced_boundary = {".github/CODEOWNERS", ".github/workflows/alpha-lab-freeze.yml"}
    assert all(active["files"].get(path) == digest for path, digest in historical["files"].items() if path not in replaced_boundary)


def test_v12_core_manifest_covers_dataset_builder_interfaces():
    active = json.loads((ROOT / "alpha_lab/freeze-manifest-core-v1.2.json").read_text())
    assert "alpha_lab/data/dataset_builder.py" in active["files"]
    assert "alpha_lab/dataset_cli.py" in active["files"]


def test_service_v3_covers_builder_schemas():
    active = json.loads((ROOT / "alpha_lab_service/protocol-manifest-v3.json").read_text())
    assert "alpha_lab/data/schemas/availability-rule-v1.schema.json" in active["files"]


def test_service_v3_covers_v2_acceptance_artifacts():
    active = json.loads((ROOT / "alpha_lab_service/protocol-manifest-v3.json").read_text())
    assert "alpha_lab_service/jobs_v2.py" in active["files"]
    assert "alpha_lab_service/acceptance_v2.py" in active["files"]
    assert "alpha_lab_service/tests/test_v2_container.py" in active["files"]
    assert "docs/alpha_lab/python-factor-execution-protocol-v2.md" in active["files"]
    assert "docs/alpha_lab/python-factor-service-v2-acceptance.md" in active["files"]
    assert "docs/alpha_lab/python-factor-service-v2-acceptance.json" in active["files"]


def test_v14_governance_tracks_active_freeze_policy():
    active = json.loads((ROOT / "alpha_lab/freeze-manifest-v1.4.json").read_text())
    assert "alpha_lab/freeze-manifest-core-v1.2.json" in active["files"]


def test_governance_rejects_changed_or_untracked_dataset_builder_file():
    from alpha_lab_service import governance
    assert not governance.verify_all()
    target = ROOT / "alpha_lab/data/dataset_builder.py"
    old = target.read_bytes()
    try:
        target.write_bytes(old + b"\n# tamper\n")
        assert any("dataset_builder.py" in error and ("changed" in error or "untracked" in error) for error in governance.verify_all())
    finally:
        target.write_bytes(old)
