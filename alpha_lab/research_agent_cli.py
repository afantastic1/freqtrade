"""No-argument research gateway for a restricted OpenCode shell permission."""

import json
import re
from pathlib import Path

from alpha_lab.cross_sectional_cli import load_bounded_panel_csv
from alpha_lab.cross_sectional_runner import run_cross_sectional
from alpha_lab.cross_sectional_store import CrossSectionalStore
from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore


ROOT = Path("user_data/research/alpha_lab")
REQUEST = ROOT / "candidates" / "current" / "request.json"
CONFIG = ROOT / "candidates" / "current" / "config.json"
ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
RUN_ID = re.compile(r"^[a-f0-9]{32}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _load_fixed_json(path: Path, parent: Path) -> dict:
    if path.is_symlink() or any(part.is_symlink() for part in path.parents if part != Path(".")):
        raise ValueError("symlink is not permitted")
    if not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError("request path escaped research directory")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def execute_request(*, root: Path = ROOT) -> dict:
    request = _load_fixed_json(root / "candidates/current/request.json", root / "candidates")
    action = request.get("action")
    db_path = root / "experiments.sqlite3"
    ExperimentRegistry(db_path)
    store = ResearchStore(db_path)
    if action == "history" and set(request) == {"action"}:
        return {"runs": store.list_runs(limit=30)}
    if action == "family_review" and set(request) == {"action"}:
        return CrossSectionalStore(db_path).family_review()
    if action == "show" and set(request) == {"action", "run_id"}:
        if not isinstance(request["run_id"], str) or not RUN_ID.fullmatch(request["run_id"]):
            raise ValueError("invalid run_id")
        return {"run": store.show_run(request["run_id"]),
                "decisions": store.list_decisions(request["run_id"])}
    if action == "cross_sectional_run" and set(request) == {"action", "panel_id"}:
        panel_id = request["panel_id"]
        if not isinstance(panel_id, str) or not ID.fullmatch(panel_id):
            raise ValueError("invalid panel_id")
        config = _load_fixed_json(root / "candidates/current/config.json", root / "candidates")
        panel_path = root / "panels" / f"{panel_id}.csv"
        if panel_path.is_symlink():
            raise ValueError("panel symlink is not permitted")
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
    raise ValueError("unsupported research request")


def main():
    print(json.dumps(execute_request(), sort_keys=True, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
