"""Data maintainer CLI. This module does not access exchange APIs or trade."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import export_market, publish, verify
from .defillama import fetch_chain_tvl, normalize_chain_tvl

DEFAULT_ROOT = Path("user_data/data_engineering")


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
    else:
        raise AssertionError(args.command)
    print(json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
