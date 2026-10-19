import hashlib
import json

from alpha_lab.freeze import build_manifest, verify_manifest
from alpha_lab.version import ENGINE_VERSION, PROTOCOL_VERSION


def test_freeze_manifest_detects_core_change(tmp_path):
    core = tmp_path / "alpha_lab" / "stats"
    core.mkdir(parents=True)
    target = core / "metric.py"
    target.write_text("stable", encoding="utf-8")
    manifest = tmp_path / "freeze.json"
    manifest.write_text(json.dumps({
        "engine_version": ENGINE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "files": {"alpha_lab/stats/metric.py": hashlib.sha256(b"stable").hexdigest()},
    }), encoding="utf-8")

    assert verify_manifest(tmp_path, manifest) == []
    target.write_text("changed", encoding="utf-8")
    assert verify_manifest(tmp_path, manifest) == ["changed: alpha_lab/stats/metric.py"]


def test_build_manifest_covers_result_affecting_modules(tmp_path):
    for relative in ("alpha_lab/runner.py", "alpha_lab/factors/builtin.py", "alpha_lab/tests/test_x.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    result = build_manifest(tmp_path, engine_version="v1")
    assert set(result["files"]) == {
        "alpha_lab/runner.py", "alpha_lab/factors/builtin.py", "alpha_lab/tests/test_x.py"
    }
