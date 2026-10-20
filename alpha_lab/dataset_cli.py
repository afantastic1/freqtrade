"""Request-driven Dataset Builder v2 command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from data_engineering.catalog_v3 import list_datasets
from data_engineering.sources.contracts import DatasetIdentity
from .data.availability import AvailabilityRuleRegistry, AvailabilityRuleV1
from .data.dataset_builder import DatasetBuilderV2
from .data.source_resolver import DatasetSourceResolver, _TYPE
from .data.temporal_plan import DatasetBuildOptionsV1


def _source_roots(values: list[str]) -> dict[str, Path]:
    roots = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--source-root must use ID=PATH")
        key, path = value.split("=", 1)
        if not key or not path or key in roots:
            raise ValueError("invalid or duplicate --source-root")
        roots[key] = Path(path)
    return roots


def _identity_map(request, exchange, catalog_root):
    records = list_datasets(catalog_root)
    result = {}
    for symbol in request.symbols:
        nested = {}
        quote = next((item for item in ("USDT", "USDC", "USD", "BTC", "ETH") if symbol.endswith(item)), None)
        if quote is None:
            raise ValueError(f"cannot map requested symbol {symbol} to a base asset")
        asset = symbol[:-len(quote)]
        for data_type in request.data_types:
            market_type, kind = _TYPE[data_type]
            timeframe = request.timeframe if kind == "ohlcv" else None
            matches = [record["manifest"] for record in records if
                       record["manifest"]["exchange"] == exchange
                       and record["manifest"]["market_type"] == market_type
                       and record["manifest"]["asset"] == asset
                       and record["manifest"]["data_kind"] == kind
                       and (record["manifest"]["timeframe"] == timeframe if timeframe else True)]
            symbols = {item["exchange_symbol"] for item in matches}
            if len(symbols) > 1:
                raise ValueError(f"ambiguous source identity for {symbol} {data_type}")
            if symbols:
                exchange_symbol = next(iter(symbols))
            else:
                exchange_symbol = f"{asset}/{quote}:{quote}" if market_type == "futures" else f"{asset}/{quote}"
            nested[data_type] = {"exchange": exchange, "market_type": market_type,
                                 "asset": asset, "exchange_symbol": exchange_symbol}
            if kind != "ohlcv":
                nested[data_type]["timeframe"] = "event"
        result[symbol] = nested
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    dataset = subparsers.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    build = dataset_commands.add_parser("build")
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--catalog-root", type=Path, required=True)
    build.add_argument("--source-root", action="append", required=True, metavar="ID=PATH")
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--missing-policy", choices=("reject", "preserve"), required=True)
    build.add_argument("--exchange", default="binance")
    build.add_argument("--primary-data-type")
    build.add_argument("--availability-delay-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        roots = _source_roots(args.source_root)
        raw = json.loads(args.request.read_text(encoding="utf-8"))
        registry = FileCampaignPolicyRegistry()
        request = ResearchRequestV2.from_dict(raw, registry)
        identities = _identity_map(request, args.exchange, args.catalog_root)
        rules = {}
        rule_ids = {}
        for symbol in request.symbols:
            for data_type in request.data_types:
                mapped = identities[symbol][data_type]
                market, kind = _TYPE[data_type]
                identity = DatasetIdentity(exchange=args.exchange, market_type=market, asset=mapped["asset"],
                                           exchange_symbol=mapped["exchange_symbol"], data_kind=kind,
                                           timeframe=request.timeframe if kind == "ohlcv" else "event")
                rule_id = f"{symbol}-{data_type}-availability-v1"
                rule_ids[f"{symbol}:{data_type}"] = rule_id
                if kind == "ohlcv":
                    rule = AvailabilityRuleV1(rule_id, identity, "fixed_delay", "date",
                                              fixed_delay=__import__("datetime").timedelta(seconds=args.availability_delay_seconds))
                else:
                    rule = AvailabilityRuleV1(rule_id, identity, "source_evidence", "date")
                rules[rule_id] = rule
        availability_registry = AvailabilityRuleRegistry(rules)
        resolver = DatasetSourceResolver(args.catalog_root, roots, exchange=args.exchange,
                                         symbol_identities=identities, availability_registry=availability_registry)
        options = DatasetBuildOptionsV1(args.missing_policy, args.primary_data_type, {}, rule_ids, {})
        builder = DatasetBuilderV2(registry, resolver, availability_registry, args.output_root)
        result = builder.build(request, options=options)
        print(json.dumps({"manifest": result.manifest, "artifact": {"worker": str(result.artifact.worker_path),
                          "labels": str(result.artifact.labels_path), "report": str(result.artifact.report_path),
                          "manifest_path": str(result.artifact.manifest_path),
                          "fingerprints": dict(result.artifact.fingerprints)}}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"dataset build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
