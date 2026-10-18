from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SKILL = Path(__file__).parents[1]
CLI = SKILL / "scripts" / "alpha.py"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_initializes_imports_and_searches(tmp_path):
    (tmp_path / "user_data" / "research").mkdir(parents=True)

    initialized = run_cli(tmp_path, "init")
    imported = run_cli(tmp_path, "catalog", "import-samples")
    repeated = run_cli(tmp_path, "catalog", "import-samples")
    searched = run_cli(tmp_path, "catalog", "search", "--query", "momentum")

    assert initialized.returncode == 0
    assert json.loads(initialized.stdout)["schema_version"] >= 2
    assert json.loads(imported.stdout)["inserted"] == 2
    assert json.loads(repeated.stdout)["existing"] == 2
    rows = json.loads(searched.stdout)["factors"]
    assert [row["factor_id"] for row in rows] == ["F-SAMPLE-MOM-024"]


def test_cli_rejects_external_database_path(tmp_path):
    result = run_cli(tmp_path, "--db", str(tmp_path / "outside.sqlite"), "init")

    assert result.returncode != 0
    assert "user_data/research" in json.loads(result.stderr)["error"]


def test_cli_records_and_lists_legacy_registry(tmp_path):
    (tmp_path / "user_data" / "research").mkdir(parents=True)
    payload = tmp_path / "legacy.json"
    payload.write_text(json.dumps({
        "id": "E001", "source": "registry.jsonl", "shape": "ab12",
        "alpha_type": None, "raw": {"id": "E001", "metrics": {"n": 11}},
    }), encoding="utf-8")

    recorded = run_cli(tmp_path, "legacy", "record", "--file", str(payload))
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["status"] == "inserted"

    listed = run_cli(tmp_path, "registry", "show", "--table", "legacy_registry")
    assert listed.returncode == 0, listed.stderr
    assert [r["id"] for r in json.loads(listed.stdout)["rows"]] == ["E001"]
