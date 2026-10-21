from pathlib import Path

from alpha_lab_service.manifest import verify_manifest
from alpha_lab_service.governance import verify_v11_core

ROOT = Path(__file__).resolve().parents[2]


def test_owner_boundary_covers_service_worker_gate_workflows_and_manifests():
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    required = (
        "/alpha_lab_service/", "/containers/alpha-lab-factor-worker/",
        "/alpha_lab_local/", "/containers/alpha-lab-local-service/",
        "/deploy/alpha-lab-local/", "/scripts/alpha_lab_wsl_start.sh",
        "/scripts/alpha_lab_wsl_stop.sh", "/examples/opencode.alpha-lab.jsonc",
        "/alpha_lab/freeze-manifest-core-v1.3.json",
        "/alpha_lab/freeze-manifest-v1.6.json",
        "/scripts/alpha_lab_admission_gate.py", "/.github/workflows/alpha-lab-freeze.yml",
        "/.github/workflows/alpha-lab-python-service.yml", "/alpha_lab/freeze-manifest-v1.3.json",
        "/alpha_lab_service/protocol-manifest-v2.json", "/alpha_lab_contracts/",
        "/docs/alpha_lab/",
        "/data_engineering/",
    )
    assert all(path in owners for path in required)


def test_ci_triggers_and_checks_all_service_security_boundaries():
    workflow = ROOT / ".github/workflows/alpha-lab-python-service.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    for path in (
        "alpha_lab_service/**", "containers/alpha-lab-factor-worker/**",
        "alpha_lab_local/**", "containers/alpha-lab-local-service/**",
        "deploy/alpha-lab-local/**", "scripts/alpha_lab_wsl_start.sh",
        "scripts/alpha_lab_wsl_stop.sh", "examples/opencode.alpha-lab.jsonc",
        "alpha_lab/freeze-manifest-core-v1.3.json",
        "alpha_lab/freeze-manifest-v1.6.json",
        "scripts/alpha_lab_admission_gate.py", ".github/CODEOWNERS",
        ".github/workflows/alpha-lab-python-service.yml", "alpha_lab/freeze-manifest-v1.3.json",
        "alpha_lab_service/protocol-manifest-v2.json", "alpha_lab_contracts/**",
        "docs/alpha_lab/python-factor-*.md",
        "docs/alpha_lab/unified-descriptive-evidence-acceptance.*",
        "docs/alpha_lab/agent-research-gateway-acceptance.*",
        "data_engineering/**",
    ):
        assert path in text
    assert "test_worker_container.py" in text
    assert "alpha_lab_service.governance" in text
    assert "ALPHA_LAB_WORKER_IMAGE" in text


def test_new_service_manifest_covers_its_verifier_worker_and_admission_gate():
    manifest = ROOT / "alpha_lab_service/protocol-manifest-v5.json"
    import json
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert not any(path.startswith("data_engineering/build/") for path in files)
    assert not any(".egg-info/" in path for path in files)
    assert verify_manifest(ROOT, manifest, manifest_version="5.0-python-factor-service") == []
    expected = {
        "alpha_lab_service/manifest.py",
        "alpha_lab_service/governance.py",
        "alpha_lab_service/evaluation_v2.py",
        "alpha_lab_service/history_v2.py",
        "containers/alpha-lab-factor-worker/worker.py",
        "scripts/alpha_lab_admission_gate.py",
        "docs/alpha_lab/python-factor-execution-protocol-v1.md",
        "alpha_lab_contracts/research_request_v2.py",
        "alpha_lab/data/schemas/availability-rule-v1.schema.json",
    }
    assert expected <= set(files)
    assert "docs/alpha_lab/unified-descriptive-evidence-acceptance.md" in files
    assert "alpha_lab_service/acceptance_agent_v2.py" in files
    assert "alpha_lab_service/tests/test_acceptance_agent_v2.py" in files
    assert "docs/alpha_lab/agent-research-gateway-acceptance.md" in files
    assert "data_engineering/catalog_v3.py" in files
    assert "data_engineering/sources/binance_vision.py" in files
    assert "data_engineering/quality/contracts.py" in files


def test_v12_governance_keeps_every_v11_frozen_core_hash_valid():
    assert verify_v11_core() == []


def test_alpha_lab_contracts_are_covered_by_ci_and_codeowners():
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    assert "/alpha_lab_contracts/" in owners
    assert "/alpha_lab_service/protocol-manifest-v2.json" in owners
    assert "/alpha_lab_service/protocol-manifest-v3.json" in owners
    assert "/alpha_lab_service/" in owners
    assert "/alpha_lab/freeze-manifest-v1.3.json" in owners
    assert "/alpha_lab/freeze-manifest-core-v1.3.json" in owners

    import json
    protected = json.loads((ROOT / "alpha_lab_service/protected-files.json").read_text(encoding="utf-8"))
    assert "/alpha_lab_contracts/" in protected["required_owner_paths"]
    assert "alpha_lab_contracts/**" in protected["required_ci_paths"]
    assert "/alpha_lab_service/protocol-manifest-v2.json" in protected["required_owner_paths"]
    assert "/alpha_lab_service/protocol-manifest-v3.json" in protected["required_owner_paths"]
    assert "/alpha_lab_service/protocol-manifest-v4.json" in protected["required_owner_paths"]
    assert "/alpha_lab_service/protocol-manifest-v5.json" in protected["required_owner_paths"]
    assert "/alpha_lab/freeze-manifest-core-v1.3.json" in protected["required_owner_paths"]
    assert "/alpha_lab/freeze-manifest-v1.6.json" in protected["required_owner_paths"]
    assert "/alpha_lab_local/" in protected["required_owner_paths"]
    assert "alpha_lab_local/**" in protected["required_ci_paths"]
    assert "alpha_lab_service/**" in protected["required_ci_paths"]
    assert "/data_engineering/" in protected["required_owner_paths"]
    assert "data_engineering/**" in protected["required_ci_paths"]

    freeze_text = (ROOT / ".github/workflows/alpha-lab-freeze.yml").read_text(encoding="utf-8")
    service_text = (ROOT / ".github/workflows/alpha-lab-python-service.yml").read_text(encoding="utf-8")
    assert "alpha_lab_contracts/**" in freeze_text and "alpha_lab_contracts/tests/" in freeze_text
    assert "data_engineering/**" in freeze_text and "data_engineering/" in freeze_text
    assert "alpha_lab/freeze-manifest-core-v1.3.json" in freeze_text
    for test_path in (
        "tests/test_data_engineering_catalog_v3.py",
        "tests/test_data_engineering_source_pipeline.py",
        "tests/test_data_engineering_source_publish.py",
        "tests/test_data_engineering_source_vision.py",
        "tests/test_data_engineering_sources_contracts.py",
    ):
        assert test_path in freeze_text
    assert "alpha_lab_contracts/**" in service_text
    assert '"alpha_lab_service/**"' in service_text
    assert '"data_engineering/**"' in service_text
    assert "alpha_lab/freeze-manifest-core-v1.3.json" in service_text


def test_active_governance_selects_v16_and_service_v5():
    import json
    from alpha_lab_service import governance

    assert governance.V16_GOVERNANCE.name == "freeze-manifest-v1.6.json"
    assert governance.SERVICE_MANIFEST.name == "protocol-manifest-v5.json"
    manifest = json.loads(governance.SERVICE_MANIFEST.read_text(encoding="utf-8"))
    assert "alpha_lab_contracts/research_request_v2.py" in manifest["files"]
