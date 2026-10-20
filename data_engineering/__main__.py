"""Data maintainer CLI. This module does not access exchange APIs or trade."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import export_market, publish, verify
from .catalog_v3 import get_coverage, list_datasets, validate_dataset
from .defillama import fetch_chain_tvl, normalize_chain_tvl
from .freqtrade_feather import discover_feather_datasets, register_feather_file
from .pit_audit import build_pit_audit, render_markdown

DEFAULT_ROOT = Path("user_data/data_engineering")


def _source_roots(values):
    roots = {}
    for value in values:
        if "=" not in value:
            raise ValueError("source roots must use SOURCE_ROOT_ID=PATH")
        root_id, path = value.split("=", 1)
        if not root_id or not path or root_id in roots:
            raise ValueError("source roots require unique IDs and nonempty paths")
        roots[root_id] = Path(path)
    return roots


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Offline-first snapshot data engineering")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = p.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory", help="List local Freqtrade Feather file metadata only")
    inventory.add_argument("--datadir", type=Path, default=Path("user_data/data/binance"))

    ingest = sub.add_parser("ingest", help="Publish a physically bounded development CSV")
    ingest.add_argument("--kind", required=True, choices=("market", "fundamental"))
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--dataset-id", required=True)
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--source-reference", default="")
    ingest.add_argument("--timeframe", choices=("5m", "15m", "1h", "4h", "1d"))

    fetch = sub.add_parser("fetch-defillama", help="Explicit steward-only HTTPS download; raw may include locked years")
    fetch.add_argument("--chain", required=True)
    fetch.add_argument("--raw-dir", type=Path, default=DEFAULT_ROOT / "raw" / "defillama")

    normalize = sub.add_parser("normalize-defillama", help="Normalize 2022-2024 chain TVL from steward-only JSON")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument("--chain", required=True)

    check = sub.add_parser("verify", help="Recompute digest and validation statistics")
    check.add_argument("--dataset-id", required=True)
    check.add_argument("--sha256", required=True)

    export = sub.add_parser("export-market", help="Copy a verified snapshot into Alpha Lab's panel directory")
    export.add_argument("--dataset-id", required=True)
    export.add_argument("--sha256", required=True)
    export.add_argument("--output", type=Path, required=True)

    pit_audit = sub.add_parser("pit-audit", help="Validate PIT evidence and build eligibility checklist")
    pit_audit.add_argument("--format", choices=("json", "markdown"), default="json")
    pit_audit.add_argument("--output", type=Path)

    catalog_v3 = sub.add_parser("catalog-v3", help="Query or register Data Catalog v3 datasets")
    v3_sub = catalog_v3.add_subparsers(dest="v3_command", required=True)
    v3_list = v3_sub.add_parser("list", help="List registered v3 dataset versions")
    v3_list.add_argument("--exchange")
    v3_list.add_argument("--market-type", choices=("spot", "futures"))
    v3_list.add_argument("--asset")
    v3_list.add_argument("--data-kind", choices=("ohlcv", "funding_rate", "open_interest", "mark_price"))
    v3_list.add_argument("--timeframe")

    v3_discover = v3_sub.add_parser("discover", help="Inspect Feather files under an explicit source root")
    v3_discover.add_argument("--source-root-id", required=True)
    v3_discover.add_argument("--source-root", type=Path, required=True)
    v3_discover.add_argument("--exchange", required=True)
    v3_discover.add_argument("--market-type", required=True, choices=("spot", "futures"))

    v3_register = v3_sub.add_parser("register", help="Register one Feather file in catalog v3")
    v3_register.add_argument("--source-root-id", required=True)
    v3_register.add_argument("--source-root", type=Path, required=True)
    v3_register.add_argument("--relative-path", type=Path, required=True)
    v3_register.add_argument("--exchange", required=True)
    v3_register.add_argument("--market-type", required=True, choices=("spot", "futures"))
    v3_register.add_argument("--asset", required=True)
    v3_register.add_argument("--exchange-symbol", required=True)
    v3_register.add_argument("--data-kind", required=True,
                              choices=("ohlcv", "funding_rate", "open_interest", "mark_price"))
    v3_register.add_argument("--timeframe")
    v3_register.add_argument("--source", required=True)
    v3_register.add_argument("--source-reference", default="")

    v3_coverage = v3_sub.add_parser("coverage", help="Query v3 coverage for a UTC interval")
    v3_coverage.add_argument("--dataset-id", required=True)
    v3_coverage.add_argument("--start", required=True)
    v3_coverage.add_argument("--end", required=True)
    v3_coverage.add_argument("--source-root", action="append", default=[], metavar="ID=PATH")

    v3_validate = v3_sub.add_parser("validate", help="Verify source integrity and dataset quality")
    v3_validate.add_argument("--dataset-id", required=True)
    v3_validate.add_argument("--source-root", action="append", default=[], metavar="ID=PATH")

    args = p.parse_args(argv)
    if args.command == "inventory":
        # Metadata only; do not read mixed-year Feather contents.
        data = [{"path": str(file), "size_bytes": file.stat().st_size} for file in
                sorted(args.datadir.rglob("*.feather")) if file.is_file() and not file.is_symlink()]
        response = {"files": data, "contents_read": False}
    elif args.command == "ingest":
        response = publish(args.root, dataset_id=args.dataset_id, kind=args.kind,
                           source=args.source, source_reference=args.source_reference,
                           input_path=args.input, timeframe=args.timeframe)
    elif args.command == "fetch-defillama":
        response = fetch_chain_tvl(args.chain, args.raw_dir)
    elif args.command == "normalize-defillama":
        response = normalize_chain_tvl(args.input, args.output, chain=args.chain)
    elif args.command == "verify":
        response = verify(args.root, dataset_id=args.dataset_id, digest=args.sha256)
    elif args.command == "export-market":
        path = export_market(args.root, dataset_id=args.dataset_id, digest=args.sha256, output=args.output)
        response = {"output": str(path), "status": "created"}
    elif args.command == "pit-audit":
        report = build_pit_audit(args.root)
        output = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
        if args.format == "markdown":
            output = render_markdown(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output.rstrip() + "\n", encoding="utf-8")
            response = {"output": str(args.output), "dataset_count": report["dataset_count"],
                        "strict_eligible_dataset_count": report["strict_eligible_dataset_count"]}
        else:
            print(output.rstrip())
            return 0
    elif args.command == "catalog-v3":
        if args.v3_command == "list":
            response = list_datasets(
                args.root, exchange=args.exchange, market_type=args.market_type,
                asset=args.asset, data_kind=args.data_kind, timeframe=args.timeframe,
            )
        elif args.v3_command == "discover":
            response = discover_feather_datasets(
                args.source_root_id, args.source_root,
                exchange=args.exchange, market_type=args.market_type,
            )
        elif args.v3_command == "register":
            response = register_feather_file(
                args.root, source_root_id=args.source_root_id, source_root=args.source_root,
                relative_path=args.relative_path, exchange=args.exchange,
                market_type=args.market_type, asset=args.asset,
                exchange_symbol=args.exchange_symbol, data_kind=args.data_kind,
                timeframe=args.timeframe, source=args.source, source_reference=args.source_reference,
            )
        elif args.v3_command == "validate":
            response = validate_dataset(
                args.root, args.dataset_id, source_roots=_source_roots(args.source_root)
            )
        elif args.v3_command == "coverage":
            response = get_coverage(
                args.root, args.dataset_id, args.start, args.end,
                source_roots=_source_roots(args.source_root),
            )
        else:
            raise AssertionError(args.v3_command)
    else:
        raise AssertionError(args.command)
    print(json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
