#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from catalog import import_samples, search_factors
from registry import connect, migrate, registry_summary, resolve_db_path
import registry_ops as OPS


def _load_json_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Controlled Freqtrade alpha research registry")
    root.add_argument("--db", help="SQLite path below user_data/research")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("inspect")

    catalog = commands.add_parser("catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    importer = catalog_commands.add_parser("import-samples")
    importer.add_argument("--dry-run", action="store_true")
    search = catalog_commands.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)

    # ---- dataset ----
    dataset = commands.add_parser("dataset")
    dcmds = dataset.add_subparsers(dest="dataset_command", required=True)
    dreg = dcmds.add_parser("register")
    dreg.add_argument("--file", required=True, help="JSON payload file")
    dreg.add_argument("--strict", action="store_true")
    dapp = dcmds.add_parser("approve")
    dapp.add_argument("--dataset-id", required=True)
    dapp.add_argument("--purpose", required=True)
    dapp.add_argument("--approved-by", required=True)
    dapp.add_argument("--run-id")

    # ---- hypothesis / factor / experiment / evaluation / selection / tradability / legacy ----
    _record_only = ("selection", "tradability", "legacy")
    for name in ("hypothesis", "factor", "experiment", "evaluation", "selection",
                 "tradability", "legacy"):
        sub = commands.add_parser(name)
        scmds = sub.add_subparsers(dest=f"{name}_command", required=True)
        add = scmds.add_parser("record" if name in _record_only else "add")
        add.add_argument("--file", required=True, help="JSON payload file")
        add.add_argument("--strict", action="store_true")

    # ---- research run ----
    run = commands.add_parser("run")
    rcmds = run.add_subparsers(dest="run_command", required=True)
    rstart = rcmds.add_parser("start")
    rstart.add_argument("--file", required=True)
    rfinish = rcmds.add_parser("finish")
    rfinish.add_argument("--run-id", required=True)
    rfinish.add_argument("--status", required=True)
    rfinish.add_argument("--file", required=True, help="JSON decision payload")
    rfinish.add_argument("--used-budget", type=int)

    # ---- registry (read-only) ----
    reg = commands.add_parser("registry")
    regcmds = reg.add_subparsers(dest="registry_command", required=True)
    regcmds.add_parser("counts")
    show = regcmds.add_parser("show")
    show.add_argument("--table", required=True)
    show.add_argument("--id")
    show.add_argument("--limit", type=int, default=50)
    show.add_argument("--timeframe", help="filter by K-line timeframe (factors/experiments/tradability)")

    # ---- batch import (single controlled write path) ----
    imp = commands.add_parser("import")
    imp.add_argument("--file", required=True, help="JSON bundle: runs/datasets/hypotheses/factors/experiments/evaluations/tradability/legacy/selections")
    imp.add_argument("--strict", action="store_true")
    return root


def execute(args: argparse.Namespace) -> dict[str, Any]:
    db_path = resolve_db_path(args.db)
    connection = connect(db_path)
    try:
        migrate(connection)
        if args.command in {"init", "inspect"}:
            return {"database": str(db_path), **registry_summary(connection)}
        if args.command == "catalog":
            if args.catalog_command == "import-samples":
                return {"database": str(db_path), **import_samples(connection, args.dry_run)}
            if args.catalog_command == "search":
                if not 1 <= args.limit <= 100:
                    raise ValueError("limit must be between 1 and 100")
                return {"database": str(db_path), "query": args.query,
                        "factors": search_factors(connection, args.query, args.limit)}
            raise ValueError("unsupported catalog command")
        if args.command == "dataset":
            if args.dataset_command == "register":
                payload = _load_json_file(args.file)
                return {"database": str(db_path),
                        **OPS.register_dataset(connection, strict=args.strict, **payload)}
            if args.dataset_command == "approve":
                return {"database": str(db_path),
                        **OPS.record_exposure(connection, dataset_id=args.dataset_id,
                                              purpose=args.purpose,
                                              approved_by=args.approved_by,
                                              run_id=args.run_id)}
            raise ValueError("unsupported dataset command")
        if args.command == "hypothesis":
            return {"database": str(db_path),
                    **OPS.add_hypothesis(connection, _load_json_file(args.file), args.strict)}
        if args.command == "factor":
            return {"database": str(db_path),
                    **OPS.add_factor(connection, _load_json_file(args.file), args.strict)}
        if args.command == "experiment":
            return {"database": str(db_path),
                    **OPS.add_experiment(connection, _load_json_file(args.file), args.strict)}
        if args.command == "evaluation":
            return {"database": str(db_path),
                    **OPS.add_evaluation(connection, _load_json_file(args.file), args.strict)}
        if args.command == "selection":
            return {"database": str(db_path),
                    **OPS.record_selection(connection, _load_json_file(args.file))}
        if args.command == "tradability":
            return {"database": str(db_path),
                    **OPS.record_tradability(connection, _load_json_file(args.file))}
        if args.command == "legacy":
            return {"database": str(db_path),
                    **OPS.record_legacy(connection, _load_json_file(args.file))}
        if args.command == "run":
            if args.run_command == "start":
                return {"database": str(db_path),
                        **OPS.start_run(connection, _load_json_file(args.file))}
            if args.run_command == "finish":
                return {"database": str(db_path),
                        **OPS.finish_run(connection, run_id=args.run_id, status=args.status,
                                         decision=_load_json_file(args.file),
                                         used_budget=args.used_budget)}
            raise ValueError("unsupported run command")
        if args.command == "registry":
            if args.registry_command == "counts":
                return {"database": str(db_path), "counts": OPS.registry_counts(connection)}
            if args.registry_command == "show":
                if not 1 <= args.limit <= 1000:
                    raise ValueError("limit must be between 1 and 1000")
                return {"database": str(db_path), "table": args.table,
                        "rows": OPS.registry_show(connection, args.table, id=args.id,
                                                  limit=args.limit, timeframe=args.timeframe)}
            raise ValueError("unsupported registry command")
        if args.command == "import":
            return {"database": str(db_path),
                    **OPS.import_bundle(connection, _load_json_file(args.file), args.strict)}
        raise ValueError("unsupported command")
    finally:
        connection.close()


def main() -> int:
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
