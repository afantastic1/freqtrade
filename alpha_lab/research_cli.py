import argparse
import json
from pathlib import Path

from alpha_lab.research_store import ResearchStore, candidate_id_for


DEFAULT_DB = Path("user_data/research/alpha_lab/experiments.sqlite3")
DEFAULT_RUNS = Path("user_data/research/alpha_lab/runs")


def backfill(
    store: ResearchStore,
    runs_root: str | Path = DEFAULT_RUNS,
    *,
    dry_run: bool = False,
) -> dict:
    """Index canonical run summaries whose completed parent rows exist."""
    counts = {
        "indexed": 0,
        "would_index": 0,
        "already_indexed": 0,
        "missing_parent": 0,
        "invalid": 0,
        "conflict": 0,
    }
    details = []
    root = Path(runs_root)
    for path in sorted(root.glob("*/summary.json")) if root.exists() else []:
        run_id = path.parent.name
        parent = store.show_run(run_id)
        if parent is None:
            counts["missing_parent"] += 1
            details.append({"run_id": run_id, "status": "missing_parent"})
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("summary is not an object")
            if raw.get("run_id") != run_id:
                raise ValueError("summary run_id mismatch")
            candidate = raw.get("candidate")
            if not isinstance(candidate, dict):
                raise ValueError("summary has no candidate")
            expected_candidate_id = candidate_id_for(candidate)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            counts["invalid"] += 1
            details.append({"run_id": run_id, "status": "invalid", "error": str(exc)})
            continue

        if parent["status"] != "completed":
            counts["invalid"] += 1
            details.append({
                "run_id": run_id,
                "status": "invalid",
                "error": f"parent status is {parent['status']}",
            })
            continue
        if parent.get("candidate_id") is not None:
            if parent["candidate_id"] == expected_candidate_id:
                counts["already_indexed"] += 1
                details.append({"run_id": run_id, "status": "already_indexed"})
            else:
                counts["conflict"] += 1
                details.append({"run_id": run_id, "status": "conflict"})
            continue
        if dry_run:
            counts["would_index"] += 1
            details.append({"run_id": run_id, "status": "would_index"})
            continue
        try:
            store.index_summary(run_id, path)
        except (ValueError, TypeError, OverflowError) as exc:
            category = (
                "conflict" if "different candidate_id" in str(exc) else "invalid"
            )
            counts[category] += 1
            details.append({"run_id": run_id, "status": category, "error": str(exc)})
        else:
            counts["indexed"] += 1
            details.append({"run_id": run_id, "status": "indexed"})
    return {"dry_run": dry_run, "counts": counts, "details": details}


def _print_output(value, *, as_json: bool):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        return
    if isinstance(value, list):
        for row in value:
            if "decision_id" in row:
                print(
                    f"{row['decision_id']}  {row['run_id']}  "
                    f"{row['created_at']}"
                )
            else:
                print(
                    f"{row['run_id']}  {row['status']}  "
                    f"{row.get('candidate_name') or '-'}  "
                    f"{row.get('classification') or '-'}"
                )
        return
    for key, item in value.items():
        if key != "details":
            print(f"{key}: {item}")


def main(argv=None, *, db_path: str | Path = DEFAULT_DB,
         runs_root: str | Path = DEFAULT_RUNS):
    parser = argparse.ArgumentParser(description="Query Alpha Lab research index")
    commands = parser.add_subparsers(dest="command", required=True)

    list_cmd = commands.add_parser("list")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--candidate")
    list_cmd.add_argument("--status", choices=("running", "completed", "failed"))
    list_cmd.add_argument("--json", action="store_true")

    show_cmd = commands.add_parser("show")
    show_cmd.add_argument("--run-id", required=True)
    show_cmd.add_argument("--json", action="store_true")

    backfill_cmd = commands.add_parser("backfill")
    backfill_cmd.add_argument("--dry-run", action="store_true")
    backfill_cmd.add_argument("--json", action="store_true")

    decide_cmd = commands.add_parser("decide")
    decide_cmd.add_argument("--run-id", required=True)
    decide_cmd.add_argument("--source-hash", required=True)
    decide_cmd.add_argument("--decision-file", type=Path, required=True)
    decide_cmd.add_argument("--json", action="store_true")

    decisions_cmd = commands.add_parser("decisions")
    decisions_cmd.add_argument("--run-id", required=True)
    decisions_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    store = ResearchStore(db_path)
    if args.command == "list":
        value = store.list_runs(
            candidate=args.candidate, status=args.status, limit=args.limit
        )
        _print_output(value, as_json=args.json)
    elif args.command == "show":
        value = store.show_run(args.run_id)
        if value is None:
            parser.error(f"unknown run_id: {args.run_id}")
        _print_output(value, as_json=args.json)
    elif args.command == "backfill":
        value = backfill(store, runs_root, dry_run=args.dry_run)
        _print_output(value, as_json=args.json)
    elif args.command == "decide":
        try:
            payload = json.loads(args.decision_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.error(f"invalid decision file: {exc}")
        if not isinstance(payload, dict):
            parser.error("decision file must contain a JSON object")
        required = {
            "supporting_evidence",
            "opposing_evidence",
            "conclusion",
            "next_hypothesis",
            "next_actions",
        }
        missing = sorted(required - payload.keys())
        if missing:
            parser.error(f"decision file missing fields: {', '.join(missing)}")
        value = store.create_decision(
            run_id=args.run_id,
            candidate_source_hash=args.source_hash,
            supporting_evidence=payload["supporting_evidence"],
            opposing_evidence=payload["opposing_evidence"],
            conclusion=payload["conclusion"],
            next_hypothesis=payload["next_hypothesis"],
            next_actions=payload["next_actions"],
            agent_identity=payload.get("agent_identity"),
        )
        _print_output(value, as_json=args.json)
    else:
        value = store.list_decisions(args.run_id)
        _print_output(value, as_json=args.json)


if __name__ == "__main__":
    main()
