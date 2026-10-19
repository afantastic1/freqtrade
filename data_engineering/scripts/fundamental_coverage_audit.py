#!/usr/bin/env python3
"""Read-only quality/coverage inventory of Data Engineering v0.1 fundamental snapshots.

Usage, from freqtrade repository root:
    .venv/bin/python data_engineering/scripts/fundamental_coverage_audit.py
    .venv/bin/python data_engineering/scripts/fundamental_coverage_audit.py --output-dir user_data/data_engineering/reports

Verifies existing v0.1 catalog hashes and schemas; NEVER downloads data, modifies
snapshots, uses OHLCV, or marks vendor history point-in-time certified.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import sys

START = date(2022, 1, 1)
END = date(2025, 1, 1)  # exclusive
DAYS = (END - START).days
ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
EXPECTED_DATASETS = (
    *[f"coinmetrics_{coin}_dev_v1" for coin in ("btc", "eth", "sol", "bnb", "xrp")],
    *[f"{chain}_chain_tvl_dev" for chain in ("bitcoin", "ethereum", "solana", "bsc", "xrpl")],
    *[f"defillama_{chain}_{metric}_dev_v1" for chain in ("solana", "bsc")
      for metric in ("dailyfees", "dailyrevenue")],
)

from data_engineering.registries import AssetRegistry, MetricRegistry

ASSET_REGISTRY = AssetRegistry()
METRIC_REGISTRY = MetricRegistry()
METRIC_DICTIONARY = {
    metric: {"meaning": definition.definition, "unit": definition.unit,
             "family": definition.family}
    for (_, metric), definition in METRIC_REGISTRY.definitions.items()
}


def _dates_and_entities(csv_path: Path, source: str) -> dict[tuple[str, str, str, str], set[date]]:
    result: dict[tuple[str, str, str, str], set[date]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_asset_id = row["asset_id"]
            try:
                metric = METRIC_REGISTRY.resolve_source(source, row["metric"])
                identity = ASSET_REGISTRY.resolve_source_id(
                    source, source_asset_id, entity_scope=metric.entity_scope
                )
            except ValueError as exc:
                raise ValueError(
                    f"Unrecognized source identity/metric {source}/{source_asset_id}/"
                    f"{row['metric']} in {csv_path}: {exc}"
                ) from exc
            when = datetime.fromisoformat(row["event_time"].replace("Z", "+00:00"))
            if when.tzinfo is None or when.utcoffset() != timedelta(0):
                raise ValueError(f"Non-UTC event in {csv_path}")
            day = when.date()
            if not START <= day < END:
                raise ValueError(f"Out-of-window event {day} in {csv_path}")
            result.setdefault((identity.asset_id, identity.tradable_asset_id,
                               identity.entity_scope, row["metric"]), set()).add(day)
    return result


def build_report(root: Path) -> dict:
    # Import local installed package, not remote source. This is the same verifier
    # used by `.venv/bin/python -m data_engineering verify`.
    from data_engineering.catalog import verify

    root = root.resolve()
    snapshots = root / "snapshots"
    if not snapshots.is_dir():
        raise FileNotFoundError(f"Snapshot catalog missing: {snapshots}")

    rows = []
    errors = []
    manifest_paths = sorted(snapshots.glob("*/*.manifest.json"))
    for manifest_path in manifest_paths:
        try:
            if manifest_path.is_symlink():
                raise ValueError("symlink manifest is forbidden")
            dataset_id = manifest_path.parent.name
            digest = manifest_path.name.removesuffix(".manifest.json")
            manifest = verify(root, dataset_id=dataset_id, digest=digest)
            if manifest["kind"] != "fundamental":
                continue
            csv_path = manifest_path.with_name(f"{digest}.csv")
            observed = _dates_and_entities(csv_path, manifest["source"])
            if sum(len(s) for s in observed.values()) != manifest["quality"]["rows"]:
                raise ValueError("manifest row count does not match distinct observations")
            for (asset_id, asset, entity_scope, metric), times in sorted(observed.items()):
                first, last = min(times), max(times)
                complete_between = (last - first).days + 1
                info = METRIC_DICTIONARY.get(metric)
                rows.append({
                    "asset": asset,
                    "asset_id": asset_id,
                    "original_asset_id": asset_id,
                    "entity_scope": entity_scope,
                    "dataset_id": dataset_id,
                    "sha256": digest,
                    "source": manifest["source"],
                    "source_reference": manifest.get("source_reference", ""),
                    "metric": metric,
                    "family": info["family"] if info else "UNREGISTERED",
                    "unit": info["unit"] if info else "UNKNOWN",
                    "metric_definition_status": "registered" if info else "needs_review",
                    "rows": len(times),
                    "first_date": first.isoformat(),
                    "last_date": last.isoformat(),
                    "window_days": DAYS,
                    "coverage_pct": round(100 * len(times) / DAYS, 2),
                    "missing_at_start": (first - START).days,
                    "internal_missing_days": complete_between - len(times),
                    "missing_at_end": (END - timedelta(days=1) - last).days,
                    "published_at_unknown_rows": manifest["quality"].get("publication_unknown_rows", 0),
                    "pit_status": manifest["quality"].get("point_in_time", "unknown"),
                    "research_safe": manifest["research_safe"],
                    "evaluation_status": "EXPLORATORY_ONLY" if not manifest["research_safe"] else "REVIEW_PROVENANCE",
                })
        except Exception as ex:
            errors.append({"manifest": str(manifest_path), "error": str(ex)})

    rows.sort(key=lambda r: (ASSETS.index(r["asset"]), r["entity_scope"], r["metric"], r["dataset_id"], r["sha256"]))
    present = sorted(set(r["asset"] for r in rows), key=ASSETS.index)
    present_ids = sorted(set(r["dataset_id"] for r in rows))
    missing_ids = sorted(set(EXPECTED_DATASETS) - set(present_ids))
    return {
        "schema_version": "fundamental-coverage-audit-v1",
        "window_start_inclusive": START.isoformat(),
        "window_end_exclusive": END.isoformat(),
        "window_days": DAYS,
        "all_requested_assets": list(ASSETS),
        "assets_with_registered_snapshots": present,
        "snapshot_manifest_count": len(manifest_paths),
        "feature_series_count": len(rows),
        "expected_dataset_count": len(EXPECTED_DATASETS),
        "present_expected_dataset_count": len(set(EXPECTED_DATASETS) & set(present_ids)),
        "missing_expected_datasets": missing_ids,
        "errors": errors,
        "metrics": rows,
        "metric_dictionary": METRIC_DICTIONARY,
        "policy": {
            "agent_data_access": "NO CHANGE: snapshots remain maintainer-controlled",
            "research_gate": "Coverage is not proof of point-in-time availability",
            "date_scope": "Development period only; no raw vendor/holdout data read",
            "market_data": "Existing Freqtrade OHLCV remains untouched",
        },
    }


def print_report(report: dict) -> None:
    print(f"Fundamental coverage {START} <= event_date < {END}; {DAYS} expected daily observations")
    print(f"Manifest files: {report['snapshot_manifest_count']}; series: {report['feature_series_count']}; errors: {len(report['errors'])}")
    print(f"Expected datasets: {report['present_expected_dataset_count']}/{report['expected_dataset_count']}")
    for name in report['missing_expected_datasets']:
        print(f"  MISSING DATASET: {name}")
    print(f"{'ASSET':<6} {'ENTITY':<11} {'METRIC':<29} {'DAYS':>5} {'COV%':>6} {'START':<10} {'END':<10} {'GAPS':>4} {'PIT':<19}")
    for r in report["metrics"]:
        print(f"{r['asset']:<6} {r['entity_scope']:<11} {r['metric'][:29]:<29} {r['rows']:>5} {r['coverage_pct']:>6.1f} {r['first_date']:<10} {r['last_date']:<10} {r['internal_missing_days']:>4} {r['pit_status']:<19}")
    if report["errors"]:
        print("\nSnapshot validation failures:", file=sys.stderr)
        for e in report["errors"]:
            print(f"  {e['manifest']}: {e['error']}", file=sys.stderr)
    if len(report['assets_with_registered_snapshots']) < len(ASSETS):
        missing = set(ASSETS) - set(report['assets_with_registered_snapshots'])
        print("Assets with no registered snapshots:", ", ".join(sorted(missing)))
    print("\nIMPORTANT: Historical vendor indicators remain exploratory until PIT publication/revisions are audited.")


def save_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # New report files; never overwrite an existing audit result.
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = output_dir / f"fundamental_coverage_{suffix}"
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    with json_path.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    with csv_path.open("x", encoding="utf-8", newline="") as f:
        if report["metrics"]:
            writer = csv.DictWriter(f, fieldnames=list(report["metrics"][0]))
            writer.writeheader()
            writer.writerows(report["metrics"])
        else:
            f.write("dataset_id,asset,metric,error\n")
    return json_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("user_data/data_engineering"))
    parser.add_argument("--output-dir", type=Path, help="Write a versioned JSON+CSV report; without it, no files are written")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.root)
        print_report(report)
        if args.output_dir:
            j, c = save_report(report, args.output_dir)
            print(f"Reports: {j} and {c}")
        return 1 if report["errors"] else 0
    except Exception as ex:
        print(f"Audit failed: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
