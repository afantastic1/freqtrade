"""Fail-closed, out-of-core Alpha Lab admission evidence tool.

This script is intentionally outside alpha_lab/ and does not run a research
factor or write the historical Research Store. It can build a bounded OHLCV
panel and verify the evidence required by a future admission decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sqlite3
import stat
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYMBOLS = (
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT",
)
TIMEFRAME = "1h"
START = pd.Timestamp("2022-01-01T00:00:00Z")
END = pd.Timestamp("2025-01-01T00:00:00Z")
AVAILABILITY_DELAY = pd.Timedelta(seconds=1)
DEFAULT_PANEL = ROOT / "user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv"
DEFAULT_DB = ROOT / "user_data/research/alpha_lab/experiments.sqlite3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files() -> dict[str, Path]:
    directory = ROOT / "user_data/data/binance/futures"
    return {
        symbol: directory / f"{symbol.split('/')[0]}_USDT_USDT-1h-futures.feather"
        for symbol in SYMBOLS
    }


def build_panel(output: Path) -> dict:
    """Use Freqtrade's existing loader; emit only rows available by 2025."""
    from alpha_lab.data.loader import load_ohlcv
    from alpha_lab.data.market_panel import MarketPanel

    output = output.resolve()
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    for existing in (output, manifest_path):
        if existing.exists():
            os.chmod(existing, 0o644)
    source_files = _source_files()
    before = {}
    for symbol, path in source_files.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"source missing or symlinked for {symbol}")
        before[symbol] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}

    assets = [
        load_ohlcv(
            symbol, TIMEFRAME, ROOT / "user_data/data/binance",
            START.isoformat(), END.isoformat(),
            availability_delay=AVAILABILITY_DELAY,
        )
        for symbol in SYMBOLS
    ]
    panel = MarketPanel.from_assets(assets)
    frame = panel.frame
    # 2024-12-31 23:00 opens inside the nominal window but is available after
    # the boundary. Exclude it so the dataset is bounded by availability too.
    frame = frame.loc[frame.available_time < END].copy()
    if frame.empty:
        raise ValueError("bounded panel is empty")
    panel = MarketPanel(frame)
    times = panel.frame.index.get_level_values("open_time")
    available = panel.frame["available_time"]
    if times.min() < START or times.max() >= END or available.max() >= END:
        raise ValueError("panel includes an out-of-window timestamp")
    got_symbols = sorted(panel.frame.index.get_level_values("symbol").unique())
    if got_symbols != sorted(SYMBOLS):
        raise ValueError("panel asset set differs from fixed five-asset set")

    quality = panel.quality_report(TIMEFRAME)
    columns = sorted(panel.frame.columns)
    missing = {
        column: int(panel.frame[column].isna().sum())
        for column in columns
    }
    row_counts = {
        symbol: int((panel.frame.index.get_level_values("symbol") == symbol).sum())
        for symbol in SYMBOLS
    }
    sections = panel.frame.index.get_level_values("open_time").nunique()
    section_counts = panel.frame.groupby(level="open_time", sort=True).size()
    source_after = {
        symbol: {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        for symbol, path in source_files.items()
    }
    if before != source_after:
        raise ValueError("source files changed while panel was being built")

    output.parent.mkdir(parents=True, exist_ok=True)
    csv_frame = panel.frame.reset_index().sort_values(
        ["open_time", "symbol"], kind="mergesort"
    )
    csv_frame.to_csv(output, index=False, float_format="%.17g", lineterminator="\n")
    # Fingerprint the serialized artifact as the research loader will read it;
    # Arrow/CSV round-trips can change pandas timestamp resolution metadata.
    from alpha_lab.cross_sectional_cli import load_bounded_panel_csv

    artifact_fingerprint = load_bounded_panel_csv(output).fingerprint()
    manifest = {
        "status": "PASS",
        "schema": "alpha-lab-panel-manifest-v1",
        "builder": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "source": "Freqtrade Binance USDⓈ-M Futures OHLCV Feather",
        "market": "Binance USDⓈ-M perpetual futures",
        "symbols": list(SYMBOLS),
        "timeframe": TIMEFRAME,
        "interval": {"start_inclusive_utc": START.isoformat(),
                     "end_exclusive_utc": END.isoformat()},
        "timestamp_semantics": {
            "open_time": "Freqtrade date; candle opening time, UTC",
            "close_time": "open_time + 1 hour",
            "available_time": "close_time + 1 second assumed publication delay; not measured",
            "row_admission": "open_time and available_time must both be earlier than 2025-01-01T00:00:00Z",
        },
        "missingness": {"fill": "none", "null_count_by_column": missing},
        "quality_by_symbol": quality,
        "rows_by_symbol": row_counts,
        "time_sections": int(sections),
        "min_assets_per_section": int(section_counts.min()),
        "max_assets_per_section": int(section_counts.max()),
        "panel_fingerprint": artifact_fingerprint,
        "panel_file": output.name,
        "panel_sha256": sha256(output),
        "panel_size_bytes": output.stat().st_size,
        "source_files": {
            symbol: {"path": str(path.relative_to(ROOT)), **before[symbol]}
            for symbol, path in source_files.items()
        },
        "source_hashes_unchanged_during_build": True,
        "reproduction": "rebuild with this script and compare panel_sha256",
        "survivorship_warning": "fixed five-asset universe; does not establish historical tradability",
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o444)
    os.chmod(manifest_path, 0o444)
    return manifest


def verify_panel(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    panel_path = manifest_path.parent / manifest["panel_file"]
    errors = []
    if manifest.get("schema") != "alpha-lab-panel-manifest-v1":
        errors.append("panel manifest schema mismatch")
    if not panel_path.is_file() or sha256(panel_path) != manifest.get("panel_sha256"):
        errors.append("panel file missing or hash mismatch")
    elif stat.S_IMODE(panel_path.stat().st_mode) != 0o444:
        errors.append("panel file is not read-only mode 0444")
    if manifest_path.is_file() and stat.S_IMODE(manifest_path.stat().st_mode) != 0o444:
        errors.append("panel manifest is not read-only mode 0444")
    for symbol, item in manifest.get("source_files", {}).items():
        source = ROOT / item["path"]
        if source.is_symlink() or not source.is_file() or sha256(source) != item.get("sha256"):
            errors.append(f"source hash mismatch: {symbol}")
    if set(manifest.get("source_files", {})) != set(SYMBOLS):
        errors.append("source inventory differs from fixed five-asset set")
    builder = manifest.get("builder", {})
    builder_path = ROOT / builder.get("path", "")
    if (not builder_path.is_file() or builder_path.is_symlink()
            or sha256(builder_path) != builder.get("sha256")):
        errors.append("panel builder hash mismatch")
    if manifest.get("symbols") != list(SYMBOLS) or manifest.get("timeframe") != TIMEFRAME:
        errors.append("fixed asset/timeframe contract mismatch")
    if manifest.get("interval") != {
        "start_inclusive_utc": START.isoformat(), "end_exclusive_utc": END.isoformat()
    }:
        errors.append("fixed time interval mismatch")
    if panel_path.is_file():
        try:
            from alpha_lab.data.market_panel import MarketPanel

            frame = pd.read_csv(panel_path)
            required = {"open_time", "symbol", "open", "high", "low", "close", "volume",
                        "close_time", "available_time"}
            if required - set(frame.columns):
                errors.append("panel required columns missing")
            else:
                for column in ("open_time", "close_time", "available_time"):
                    frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
                panel = MarketPanel(frame.set_index(["open_time", "symbol"]).sort_index())
                times = panel.frame.index.get_level_values("open_time")
                if (times.min() < START or times.max() >= END
                        or panel.frame.available_time.max() >= END):
                    errors.append("panel contains out-of-window open/availability time")
                symbols = sorted(panel.frame.index.get_level_values("symbol").unique())
                if symbols != sorted(SYMBOLS):
                    errors.append("panel contents differ from fixed asset set")
                if panel.frame[list(required - {"open_time", "symbol"})].isna().any().any():
                    errors.append("panel contains null required values")
                rows = {
                    symbol: int((panel.frame.index.get_level_values("symbol") == symbol).sum())
                    for symbol in SYMBOLS
                }
                if rows != manifest.get("rows_by_symbol"):
                    errors.append("panel row counts differ from manifest")
                if panel.fingerprint() != manifest.get("panel_fingerprint"):
                    errors.append("panel content fingerprint mismatch")
                if not panel.frame.groupby(level="open_time").size().eq(5).all():
                    errors.append("panel has incomplete five-asset sections")
        except Exception as exc:
            errors.append(f"panel content verification failed: {type(exc).__name__}")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "panel_sha256": manifest.get("panel_sha256"), "panel_path": str(panel_path)}


def _store_gate(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"status": "BLOCKED", "reason": "store file missing"}
    uri = f"file:{db_path.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = db.execute("PRAGMA foreign_key_check").fetchall()
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = [row[0] for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger'") if row[0]]
    required_tables = {"cs_evaluations", "cs_research_decisions"}
    append_only = all(
        any("BEFORE UPDATE" in sql.upper() and table in sql for sql in triggers)
        and any("BEFORE DELETE" in sql.upper() and table in sql for sql in triggers)
        for table in required_tables
    )
    ok = integrity == "ok" and not foreign and required_tables <= tables and append_only
    return {"status": "PASS" if ok else "BLOCKED", "integrity_check": integrity,
            "foreign_key_errors": len(foreign), "required_tables_present": sorted(required_tables & tables),
            "append_only_triggers_verified": append_only, "sha256": sha256(db_path)}


def evaluate_gate(*, panel_manifest: Path, qualification: Path,
                  isolation: Path, db_path: Path, method_id: str,
                  output: Path) -> dict:
    from alpha_lab.freeze import verify_manifest
    from alpha_lab.stats.method_registry import method_entry

    core_errors = verify_manifest(ROOT, ROOT / "alpha_lab/freeze-manifest-v1.1.json")
    core = {"status": "PASS" if not core_errors else "FAIL", "errors": core_errors}
    panel = verify_panel(panel_manifest)
    qdoc = json.loads(qualification.read_text(encoding="utf-8"))
    q = qdoc.get("methods", {}).get(method_id, {})
    registry = method_entry(method_id) or {}
    inference = {
        "method_id": method_id,
        "status": "PASS" if q.get("status") == "QUALIFIED"
        and registry.get("status") == "PASS" and registry.get("role") == "inference"
        else "BLOCKED",
        "qualification_status": q.get("status", "MISSING"),
        "registry_status": registry.get("status", "UNSUPPORTED"),
    }
    isolation_doc = json.loads(isolation.read_text(encoding="utf-8"))
    required_tests = {
        "core_write_denied", "market_read_denied", "evidence_read_denied",
        "decision_read_denied", "decision_write_denied", "heldout_read_denied",
        "bounded_panel_read_only_verified", "agent_workspace_write_allowed",
        "shell_spawn_denied", "symlink_escape_denied",
        "network_denied", "actual_agent_runtime_enforced",
    }
    test_results = isolation_doc.get("negative_tests", {})
    isolation_ok = isolation_doc.get("status") == "PASS" and all(
        test_results.get(test) is True for test in required_tests
    )
    isolation_result = {"status": "PASS" if isolation_ok else "BLOCKED",
                        "required_tests": {name: test_results.get(name, False)
                                           for name in sorted(required_tests)},
                        "runtime": isolation_doc.get("runtime", "unknown")}
    store = _store_gate(db_path)
    gates = {"core_manifest": core, "data_panel": panel,
             "inference_method": inference, "agent_isolation": isolation_result,
             "evidence_decision_store": store}
    admitted = all(value["status"] == "PASS" for value in gates.values())
    result = {
        "schema": "alpha-lab-admission-verdict-v1",
        "scope": "cross-sectional exploratory research, five-asset 1h 2022-2024",
        "status": "ADMITTED" if admitted else "RC_BLOCKED",
        "admitted": admitted,
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False,
            capture_output=True, text=True,
        ).stdout.strip(),
        "input_sha256": {
            "panel_manifest": sha256(panel_manifest),
            "inference_qualification": sha256(qualification),
            "agent_isolation_attestation": sha256(isolation),
        },
        "gates": gates,
        "stop_rule": "Any required gate other than PASS keeps status RC_BLOCKED.",
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-panel")
    build.add_argument("--output", type=Path, default=DEFAULT_PANEL)
    verify = sub.add_parser("verify-panel")
    verify.add_argument("--manifest", type=Path, required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--panel-manifest", type=Path, required=True)
    gate.add_argument("--qualification", type=Path, required=True)
    gate.add_argument("--isolation", type=Path, required=True)
    gate.add_argument("--db", type=Path, default=DEFAULT_DB)
    gate.add_argument("--method-id", required=True)
    gate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build-panel":
        result = build_panel(args.output)
    elif args.command == "verify-panel":
        result = verify_panel(args.manifest)
    else:
        result = evaluate_gate(panel_manifest=args.panel_manifest,
                               qualification=args.qualification,
                               isolation=args.isolation, db_path=args.db,
                               method_id=args.method_id, output=args.output)
    print(json.dumps(result, sort_keys=True))
    if args.command == "build-panel":
        return 0
    return 0 if result.get("status") in ("PASS", "ADMITTED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
