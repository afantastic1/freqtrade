"""No-argument research gateway for a restricted OpenCode shell permission."""

import json
import re
from pathlib import Path

from alpha_lab.cross_sectional_cli import load_bounded_panel_csv
from alpha_lab.cross_sectional_runner import _validate_research_window, run_cross_sectional
from alpha_lab.cross_sectional_store import CrossSectionalStore
from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore


ROOT = Path("user_data/research/alpha_lab")
REQUEST = ROOT / "candidates" / "current" / "request.json"
CONFIG = ROOT / "candidates" / "current" / "config.json"
ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
RUN_ID = re.compile(r"^[a-f0-9]{32}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _public_value(value):
    """Strip internal paths, manifests and exception text from gateway output."""
    if isinstance(value, dict):
        return {
            key: _public_value(item)
            for key, item in value.items()
            if not any(token in key.lower() for token in ("path", "error", "manifest", "traceback"))
        }
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value


def _load_fixed_json(path: Path, parent: Path) -> dict:
    if path.is_symlink() or any(part.is_symlink() for part in path.parents if part != Path(".")):
        raise ValueError("symlink is not permitted")
    if not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError("request path escaped research directory")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def _validate_panel_path(path: Path, parent: Path) -> None:
    """Reject direct and ancestor symlinks before opening a panel file."""
    resolved_parent = parent.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_parent):
        raise ValueError("panel path escaped trusted panel directory")
    current = path
    while current == parent or current.is_relative_to(parent):
        if current.is_symlink():
            raise ValueError("panel symlink is not permitted")
        if current == parent:
            break
        current = current.parent


def execute_request(*, root: Path = ROOT) -> dict:
    request = _load_fixed_json(root / "candidates/current/request.json", root / "candidates")
    action = request.get("action")
    db_path = root / "experiments.sqlite3"
    ExperimentRegistry(db_path)
    store = ResearchStore(db_path)
    if action == "history" and set(request) == {"action"}:
        return {"runs": _public_value(store.list_runs(limit=30))}
    if action == "family_review" and set(request) == {"action"}:
        return CrossSectionalStore(db_path).family_review()
    if action == "show" and set(request) == {"action", "run_id"}:
        if not isinstance(request["run_id"], str) or not RUN_ID.fullmatch(request["run_id"]):
            raise ValueError("invalid run_id")
        return {"run": _public_value(store.show_run(request["run_id"])),
                "decisions": _public_value(store.list_decisions(request["run_id"]))}
    if action == "cross_sectional_run" and set(request) == {"action", "panel_id"}:
        panel_id = request["panel_id"]
        if not isinstance(panel_id, str) or not ID.fullmatch(panel_id):
            raise ValueError("invalid panel_id")
        config = _load_fixed_json(root / "candidates/current/config.json", root / "candidates")
        panel_path = root / "panels" / f"{panel_id}.csv"
        _validate_panel_path(panel_path, root / "panels")
        _validate_research_window(config)
        panel = load_bounded_panel_csv(panel_path)
        summary = run_cross_sectional(config, panel, root=root)
        return {"run_id": summary["run_id"], "mode": summary["mode"],
                "config_hash": summary["config_hash"], "results": summary["results"]}
    if action == "decide" and set(request) == {"action", "run_id", "source_hash", "decision"}:
        run_id = request["run_id"]
        source_hash = request["source_hash"]
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        if not isinstance(source_hash, str) or not SHA256.fullmatch(source_hash):
            raise ValueError("invalid source_hash")
        decision = request["decision"]
        if not isinstance(decision, dict) or set(decision) != {
            "supporting_evidence", "opposing_evidence", "conclusion",
            "next_hypothesis", "next_actions", "agent_identity",
        }:
            raise ValueError("invalid decision fields")
        return {"decision": store.create_decision(run_id=run_id,
                                                   candidate_source_hash=source_hash,
                                                   **decision)}
    if action == "decide" and set(request) == {
        "action", "run_id", "source_hash", "decision", "evidence_id"
    }:
        run_id = request["run_id"]
        source_hash = request["source_hash"]
        evidence_id = request["evidence_id"]
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        if not isinstance(source_hash, str) or not SHA256.fullmatch(source_hash):
            raise ValueError("invalid source_hash")
        if not isinstance(evidence_id, str) or not SHA256.fullmatch(evidence_id):
            raise ValueError("invalid evidence_id")
        decision = request["decision"]
        if not isinstance(decision, dict) or set(decision) != {
            "supporting_evidence", "opposing_evidence", "conclusion",
            "next_hypothesis", "next_actions", "agent_identity",
        }:
            raise ValueError("invalid decision fields")
        return {"decision": store.create_decision(
            run_id=run_id, candidate_source_hash=source_hash,
            evidence_id=evidence_id, **decision,
        )}
    raise ValueError("unsupported research request")


def main():
    try:
        result = execute_request()
        output = json.dumps(result, sort_keys=True, default=str, allow_nan=False)
    except Exception:
        # Public gateway output must not expose raw panel values, source paths,
        # stack traces, or runner errors that may contain data-derived text.
        print(json.dumps({"error": "request rejected or failed; no result was returned"}))
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
