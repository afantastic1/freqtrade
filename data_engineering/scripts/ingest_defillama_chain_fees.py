#!/usr/bin/env python3
"""Audit and publish bounded DefiLlama ecosystem fees/revenue snapshots.

Run with the Freqtrade project's venv; steward-only raw responses must remain private.
Does not read or modify OHLCV/Alpha Lab.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEGIN = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 1, 1, tzinfo=timezone.utc)
CHAINS = ("Solana", "BSC")
METRICS = {"dailyFees": "ecosystem_daily_fees_usd", "dailyRevenue": "ecosystem_daily_revenue_usd"}


def audit_and_normalize(root: Path, batch: Path):
    daily = {}
    checksums = {}
    for chain in CHAINS:
        for name in METRICS:
            path = batch / f"{chain}_{name}.json"
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError(f"{path}: expected JSON object")
            declared = doc.get("dataType")
            if declared is not None and declared != name:
                raise ValueError(f"{path}: dataType is {declared!r}, expected {name!r}")
            chart = doc.get("totalDataChart")
            if not isinstance(chart, list):
                raise ValueError(f"{path}: totalDataChart missing")
            values = {}
            for pair in chart:
                if not isinstance(pair, list) or len(pair) < 2:
                    raise ValueError(f"{path}: malformed chart point")
                timestamp = datetime.fromtimestamp(int(pair[0]), tz=timezone.utc)
                if not BEGIN <= timestamp < END or pair[1] is None:
                    continue
                if timestamp.hour or timestamp.minute or timestamp.second or timestamp.microsecond:
                    raise ValueError(f"{path}: daily observation is not UTC midnight")
                value = float(pair[1])
                if not math.isfinite(value):
                    raise ValueError(f"{path}: nonfinite number")
                if timestamp in values:
                    raise ValueError(f"{path}: duplicate date")
                values[timestamp] = value
            if not values:
                raise ValueError(f"{path}: no historical data in 2022–2024")
            daily[chain, name] = values
            checksums[chain, name] = digest
            expected = {BEGIN + timedelta(days=i) for i in range((END-BEGIN).days)}
            absent = sorted(expected - values.keys())
            inner = [d for d in absent if min(values) < d < max(values)]
            print(f"{chain:7} {name:12} rows={len(values):4} first={min(values).date()} last={max(values).date()} missing={len(absent)} internal_gaps={len(inner)} zero_days={sum(v==0 for v in values.values())}")
            if absent:
                print("  first missing dates:", ", ".join(d.date().isoformat() for d in absent[:10]))
        fees = daily[chain, "dailyFees"]
        rev = daily[chain, "dailyRevenue"]
        common = sorted(fees.keys() & rev.keys())
        identical = sum(fees[d] == rev[d] for d in common)
        print(f"{chain:7} same_values_on_common_dates={identical}/{len(common)}")
        if common and identical == len(common):
            raise ValueError(f"{chain}: fees/revenue curves identical, check API query or raw files")
    return daily, checksums


def publish(root: Path, daily, checksums):
    output_dir = root / "processed" / "defillama_fees"
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for (chain, name), values in sorted(daily.items()):
        source_sha = checksums[chain, name]
        metric = METRICS[name]
        dataset_id = f"defillama_{chain.lower()}_{name.lower()}_dev_v1"
        out = output_dir / f"{chain.lower()}_{name}_{source_sha[:16]}.csv"
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["asset_id", "metric", "event_time", "value", "published_at"])
        for dt, value in sorted(values.items()):
            writer.writerow([f"chain:{chain}", metric, dt.isoformat(), format(value, ".17g"), ""])
        content = stream.getvalue().encode("utf-8")
        if out.exists():
            if out.read_bytes() != content:
                raise ValueError(f"Existing processed file was changed: {out}")
        else:
            with out.open("xb") as f:
                f.write(content)
        command = [sys.executable, "-m", "data_engineering", "--root", str(root), "ingest", "--kind", "fundamental",
                   "--dataset-id", dataset_id, "--source", "defillama-chain-fees-overview",
                   "--source-reference", source_sha, "--input", str(out)]
        manifest = json.loads(subprocess.check_output(command, text=True))
        check = [sys.executable, "-m", "data_engineering", "--root", str(root), "verify", "--dataset-id", dataset_id, "--sha256", manifest["sha256"]]
        verified = json.loads(subprocess.check_output(check, text=True))
        if verified["research_safe"] is not False or verified["quality"]["point_in_time"] != "unverified":
            raise ValueError("Historical vendor data incorrectly marked PIT-safe")
        results.append({"dataset_id": dataset_id, "rows": verified["quality"]["rows"],
                        "snapshot_sha256": manifest["sha256"], "research_safe": False})
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("user_data/data_engineering"))
    parser.add_argument("--batch", type=Path, help="Explicit raw download batch directory")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    raw = args.root / "raw" / "defillama" / "fees"
    if args.batch:
        batch = args.batch
    else:
        candidates = sorted(d for d in raw.iterdir() if d.is_dir() and all((d/f"{c}_{m}.json").is_file() for c in CHAINS for m in METRICS))
        if not candidates:
            parser.error("No complete four-file batch found")
        batch = candidates[-1]
    print("Raw batch:", batch)
    daily, checksums = audit_and_normalize(args.root, batch)
    if args.audit_only:
        print("Audit complete; no snapshots created")
    else:
        publish(args.root, daily, checksums)


if __name__ == "__main__":
    main()
