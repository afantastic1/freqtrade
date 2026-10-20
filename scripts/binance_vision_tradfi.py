#!/usr/bin/env python3
"""Download Binance Vision USD-M futures monthly OHLCV to Freqtrade Feather.

Safe discovery: S3 archive listings show that a *file* exists, not that a
contract is TradFi. Only manually categorized symbols are eligible for import.
Uncategorized exchange symbols are reported, NEVER automatically imported.

This tool uses public historical archives, not the Binance trading API. It is
for permitted, non-production research only (see Binance Vision Dataset Terms).

Examples:
  # Original behavior, archive existence only:
  python scripts/binance_vision_tradfi.py --check-only --start 2026-09 --end 2026-09

  # Discover available archive months; restricted to categorized oil symbols:
  python scripts/binance_vision_tradfi.py --discover --category oil --check-only

  # Download all reviewed semiconductor symbols, all available complete months:
  python scripts/binance_vision_tradfi.py --discover --category semiconductor \\
      --timeframes 15m 1h 4h

  # All *curated* oil + semiconductor symbols, NOT all Binance futures:
  python scripts/binance_vision_tradfi.py --all --start 2026-09 --end 2026-09

  # Add future listings through a reviewed local JSON file:
  python scripts/binance_vision_tradfi.py --discover --catalog my_symbols.json --all

Catalog example (additional reviewed symbols; merges with defaults):
  {"oil": ["NEWOILUSDT"], "semiconductor": ["NEWCHIPUSDT"]}

Dependencies for import (not for --check-only): pandas, numpy, pyarrow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
S3_BUCKET_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_ROOT_PREFIX = "data/futures/um/monthly/klines/"
DEFAULT_SYMBOLS = ("CLUSDT", "BZUSDT", "NVDAUSDT", "AMDUSDT", "SMHUSDT")
# Reviewed *candidates*, not proof of current market availability or contract type.
# Discovery only admits these symbols when actual archive files exist.
CURATED_CATALOG = {
    "oil": ("CLUSDT", "BZUSDT", "NATGASUSDT"),
    "semiconductor": (
        "NVDAUSDT", "AMDUSDT", "TSMUSDT", "MUUSDT", "INTCUSDT", "ASMLUSDT",
        "SMHUSDT", "SOXLUSDT",
    ),
}
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
)
USER_AGENT = "AlphaLab-BinanceVision-ArchiveImporter/2.0"
SYMBOL_PATTERN = re.compile(r"[A-Z0-9]{2,24}USDT\Z")
MONTH_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def last_complete_month() -> str:
    now = datetime.now(timezone.utc)
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return f"{year:04d}-{month:02d}"


def months_between(start: str, end: str) -> list[str]:
    if not MONTH_PATTERN.fullmatch(start) or not MONTH_PATTERN.fullmatch(end):
        raise ValueError("--start/--end must be YYYY-MM")
    if start > end:
        raise ValueError("--start must not be later than --end")
    if end > last_complete_month():
        raise ValueError("Monthly archives are only requested for completed UTC months")
    result = []
    year, month = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (year, month) <= (ey, em):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def fetch(url: str, *, method: str, retries: int = 2, timeout: int = 25):
    """Returns (HTTP status, bytes|None); 404 is a normal missing archive."""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status), resp.read() if method == "GET" else None
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 451):
                return exc.code, None
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt == retries:
                raise
        time.sleep(min(2 ** attempt, 4))
    raise RuntimeError("download retries exhausted")


def validate_symbol(symbol: str) -> str:
    candidate = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(candidate):
        raise ValueError(f"bad Binance USD-M symbol: {symbol!r}")
    return candidate


def load_catalog(path: Path | None) -> dict[str, tuple[str, ...]]:
    if path is None:
        return dict(CURATED_CATALOG)
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not obj:
        raise ValueError("catalog must be JSON object with oil and/or semiconductor arrays")
    if set(obj) - set(CURATED_CATALOG):
        raise ValueError("catalog contains unknown category; use oil or semiconductor")
    result = dict(CURATED_CATALOG)
    for name, entries in obj.items():
        if not isinstance(entries, list) or not entries or not all(isinstance(x, str) for x in entries):
            raise ValueError(f"catalog {name}: expected nonempty string array")
        result[name] = tuple(dict.fromkeys([*result[name], *(validate_symbol(x) for x in entries)]))
    return result


def selected_symbols(args, catalog: dict[str, tuple[str, ...]]) -> list[str]:
    if args.symbols:
        return list(dict.fromkeys(validate_symbol(x) for x in args.symbols))
    if args.all or args.category == "all":
        return list(dict.fromkeys(x for values in catalog.values() for x in values))
    if args.category:
        return list(catalog.get(args.category, ()))
    return list(DEFAULT_SYMBOLS)


def s3_list_prefixes(prefix: str, *, max_pages: int = 8) -> list[str]:
    """Read a bounded public S3 XML listing. Do not parse the Vision HTML page.

    Anonymous S3 bucket listing is an ancillary interface, not the FAPI endpoint.
    This operation is opt-in (--discover) and fails closed if not permitted.
    """
    seen = set()
    marker = ""
    for page in range(max_pages):
        params = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
        if marker:
            params["marker"] = marker
        url = S3_BUCKET_LIST + "?" + urllib.parse.urlencode(params)
        status, body = fetch(url, method="GET", retries=1, timeout=30)
        if status != 200 or not body:
            raise RuntimeError(f"public S3 listing unavailable: HTTP {status}")
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("oversized discovery XML")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ValueError("S3 listing returned invalid XML") from exc
        if not root.tag.endswith("ListBucketResult"):
            raise ValueError("S3 listing returned an unexpected document")
        prefixes = [x.text for x in root.findall(".//{*}CommonPrefixes/{*}Prefix") if x.text]
        keys = [x.text for x in root.findall(".//{*}Contents/{*}Key") if x.text]
        for item in prefixes + keys:
            if not item.startswith(prefix):
                raise ValueError("S3 listing returned a key outside requested prefix")
            seen.add(item)
        truncated = (root.findtext("{*}IsTruncated") or "").lower() == "true"
        if not truncated:
            return sorted(seen)
        next_marker = root.findtext("{*}NextMarker")
        if not next_marker:
            next_marker = (prefixes + keys)[-1] if prefixes or keys else ""
        if not next_marker or next_marker <= marker:
            raise ValueError("S3 listing pagination did not advance")
        marker = next_marker
        if page + 1 < max_pages:
            time.sleep(0.3)  # bounded sequential metadata requests
    raise ValueError("S3 listing exceeded page limit; refusing incomplete discovery")


def discover_archive_symbols() -> list[str]:
    entries = s3_list_prefixes(S3_ROOT_PREFIX)
    names = []
    for entry in entries:
        tail = entry[len(S3_ROOT_PREFIX):].strip("/")
        if "/" not in tail and SYMBOL_PATTERN.fullmatch(tail):
            names.append(tail)
    return sorted(set(names))


def discover_archive_months(symbol: str, timeframe: str) -> list[str]:
    prefix = f"{S3_ROOT_PREFIX}{symbol}/{timeframe}/"
    entries = s3_list_prefixes(prefix)
    pattern = re.compile(rf"{re.escape(prefix + symbol + '-' + timeframe + '-')}"
                         r"(\d{4}-\d{2})\.zip\Z")
    months = set()
    for entry in entries:
        match = pattern.fullmatch(entry)
        if match and MONTH_PATTERN.fullmatch(match.group(1)):
            months.add(match.group(1))
    return sorted(months)


def archive_url(symbol: str, timeframe: str, month: str) -> tuple[str, str]:
    name = f"{symbol}-{timeframe}-{month}.zip"
    return f"{BASE}/{symbol}/{timeframe}/{name}", name


def checksum_expected(checksum: bytes, archive_name: str) -> str:
    content = checksum.decode("utf-8-sig", errors="strict").strip()
    for line in content.splitlines():
        match = re.fullmatch(r"([A-Fa-f0-9]{64})\s+\*?(.+)", line.strip())
        if match and Path(match.group(2)).name == archive_name:
            return match.group(1).lower()
    raise ValueError(f"bad checksum file, missing expected {archive_name}")


def download_archive(*, url: str, name: str, raw_dir: Path):
    """Always validate ZIP with the fetched official CHECKSUM before import."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / name
    checksum_path = raw_dir / (name + ".CHECKSUM")
    # Recheck checksum upstream: historical archives may be corrected after release.
    status, checksum = fetch(url + ".CHECKSUM", method="GET")
    if status == 404:
        # Distinguish an unlisted month from a listed ZIP without its checksum.
        archive_status, _ = fetch(url, method="HEAD")
        if archive_status == 404:
            return "missing_archive", None, None
        if archive_status == 200:
            return "missing_checksum", None, None
        return f"archive_http_{archive_status}", None, None
    if status != 200 or checksum is None:
        return f"checksum_http_{status}", None, None
    expected = checksum_expected(checksum, name)
    if zip_path.exists() and sha256_file(zip_path) == expected:
        checksum_path.write_bytes(checksum)
        return "cached", zip_path, expected
    status, body = fetch(url, method="GET")
    if status == 404:
        return "missing_archive", None, None
    if status != 200 or body is None:
        return f"archive_http_{status}", None, None
    if sha256(body) != expected:
        raise ValueError(f"CHECKSUM mismatch: {name}")
    part = zip_path.with_suffix(zip_path.suffix + ".part")
    try:
        part.write_bytes(body)
        part.replace(zip_path)
        checksum_path.write_bytes(checksum)
    finally:
        part.unlink(missing_ok=True)
    return "downloaded", zip_path, expected


def parse_kline_zip(zip_path: Path, *, month: str, timeframe: str):
    import numpy as np
    import pandas as pd

    with zipfile.ZipFile(zip_path) as z:
        members = [x for x in z.infolist() if not x.is_dir() and x.filename.endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected exactly one CSV in {zip_path.name}, got {len(members)}")
        member = members[0]
        if member.file_size > 120 * 1024 * 1024:
            raise ValueError("oversized uncompressed CSV")
        with z.open(member) as stream:
            df = pd.read_csv(stream, header=None, dtype=str)

    if df.shape[1] != len(COLUMNS):
        raise ValueError(f"unexpected CSV column count {df.shape[1]}; expected 12")
    df.columns = list(COLUMNS)
    if len(df) and str(df.iloc[0]["open_time"]).strip().lower() in ("open_time", "open time"):
        df = df.iloc[1:].reset_index(drop=True)
    if df.empty:
        raise ValueError("empty CSV")

    ticks = pd.to_numeric(df["open_time"], errors="raise")
    if ticks.isna().any() or not np.isfinite(ticks).all():
        raise ValueError("invalid open_time")
    unit = "us" if float(ticks.median()) >= 1e14 else "ms"
    date = pd.to_datetime(ticks, unit=unit, utc=True, errors="raise")
    start = pd.Timestamp(month + "-01", tz="UTC")
    year, mm = map(int, month.split("-"))
    end = pd.Timestamp(year=year + (mm == 12), month=1 if mm == 12 else mm + 1,
                       day=1, tz="UTC")
    if bool(((date < start) | (date >= end)).any()):
        raise ValueError("archive has K-line open times outside requested calendar month")
    if bool(date.duplicated().any()) or not date.is_monotonic_increasing:
        raise ValueError("duplicate or out-of-order K-line open times")
    ts = date.astype("int64")
    if bool((ts % (TIMEFRAME_SECONDS[timeframe] * 1_000_000_000) != 0).any()):
        raise ValueError("misaligned K-line timestamps")

    out = pd.DataFrame({"date": date})
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(df[col], errors="raise").astype("float64").to_numpy()
    vals = out[["open", "high", "low", "close", "volume"]].to_numpy()
    if not np.isfinite(vals).all():
        raise ValueError("NaN or infinite OHLCV")
    if bool((out[["open", "high", "low", "close"]] <= 0).any().any()):
        raise ValueError("nonpositive OHLC price")
    if bool((out["volume"] < 0).any()):
        raise ValueError("negative volume")
    if bool(((out["high"] < out[["open", "close", "low"]].max(axis=1)) |
             (out["low"] > out[["open", "close", "high"]].min(axis=1))).any()):
        raise ValueError("inconsistent OHLC ordering")
    return out[["date", "open", "high", "low", "close", "volume"]]


def gaps_summary(frame, timeframe: str):
    import pandas as pd

    if len(frame) < 2:
        return {"gap_intervals": 0, "gap_bars": 0, "examples": []}
    step = pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    diffs = frame["date"].diff().iloc[1:]
    is_gap = diffs > step
    examples = []
    for idx in diffs[is_gap].index[:10]:
        examples.append({"after": frame.loc[idx - 1, "date"].isoformat(),
                         "before": frame.loc[idx, "date"].isoformat()})
    return {"gap_intervals": int(is_gap.sum()),
            "gap_bars": int(((diffs[is_gap] // step) - 1).sum()),
            "examples": examples,
            "note": "Gaps can be TradFi sessions or missing records; they are NOT filled."}


def export_feather(frame, *, symbol: str, timeframe: str, datadir: Path,
                   overwrite: bool):
    if not symbol.endswith("USDT"):
        raise ValueError("expected USDT-margined contract symbol")
    pair = f"{symbol[:-4]}_USDT_USDT"
    output = datadir / "futures" / f"{pair}-{timeframe}-futures.feather"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".feather", delete=False) as fh:
        temp = Path(fh.name)
    try:
        frame.to_feather(temp, compression="lz4")
        digest = sha256_file(temp)
        if output.exists() and not overwrite:
            if sha256_file(output) == digest:
                return output, "already_identical"
            raise FileExistsError(f"{output} exists; use --overwrite after backup")
        temp.replace(output)
        return output, "written"
    finally:
        temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--symbols", nargs="+", help="Explicit Binance IDs, e.g. CLUSDT NVDAUSDT")
    group.add_argument("--category", choices=("oil", "semiconductor", "all"),
                       help="Use vetted category candidate catalog")
    group.add_argument("--all", action="store_true", help="Both curated categories, NOT all Binance contracts")
    parser.add_argument("--discover", action="store_true",
                        help="Opt-in public S3 directory discovery; skip missing archives")
    parser.add_argument("--catalog", type=Path,
                        help="JSON category additions merged with built-in catalog; review new symbols first")
    parser.add_argument("--timeframes", nargs="+", default=["1h"], choices=list(TIMEFRAME_SECONDS))
    parser.add_argument("--start", help="Inclusive YYYY-MM; --discover without start auto-detects first archive")
    parser.add_argument("--end", help="Inclusive YYYY-MM; --discover default: latest complete month")
    parser.add_argument("--datadir", type=Path, default=Path("user_data/data/binance_tradfi"))
    parser.add_argument("--check-only", action="store_true", help="No ZIP download or Feather writes")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing Feather")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        symbols = selected_symbols(args, catalog)
        if not symbols:
            raise ValueError("no curated symbols for this category")
        end = args.end or (last_complete_month() if args.discover else "2026-09")
        if not MONTH_PATTERN.fullmatch(end) or end > last_complete_month():
            raise ValueError("--end must be a completed UTC month YYYY-MM")
        # Discovery gets actual listed months; absent --start means earliest available.
        # Fixed mode retains v1's default of Sep 2026.
        start = args.start or ("2026-01" if args.discover else "2026-09")
        months = months_between(start, end)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    report = {
        "schema": "binance-vision-tradfi-import-v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_base": BASE,
        "discovery_source": S3_BUCKET_LIST if args.discover else None,
        "catalog_source": str(args.catalog) if args.catalog else "built_in_curated_candidates",
        "catalog_is_exchange_verification": False,
        "symbols": symbols, "timeframes": args.timeframes,
        "requested_months": {"start": start, "end": end},
        "mode": "check_only" if args.check_only else "download_and_convert",
        "discovery": {}, "archives": [], "datasets": [], "errors": [],
        "usage_warning": "Binance Vision datasets have non-commercial/non-production restrictions; not cleared for live trading."
    }
    available: dict[tuple[str, str], set[str]] = {}
    first_listed: dict[tuple[str, str], str | None] = {}
    if args.discover:
        try:
            on_archive = set(discover_archive_symbols())
            unknown = sorted(on_archive - {x for values in catalog.values() for x in values})
            report["discovery"] = {
                "status": "ok", "archive_symbol_count": len(on_archive),
                "selected_on_archive": sorted(on_archive & set(symbols)),
                "unclassified_symbols": unknown,
                "note": "Unclassified also includes crypto; classification cannot be inferred from archive names."
            }
            print(f"[DISCOVER] archived symbols={len(on_archive)}, "
                  f"selected found={len(on_archive & set(symbols))}, "
                  f"unclassified={len(unknown)}", flush=True)
            for symbol in symbols:
                for tf in args.timeframes:
                    if symbol not in on_archive:
                        available[(symbol, tf)] = set()
                        first_listed[(symbol, tf)] = None
                        continue
                    listed = set(discover_archive_months(symbol, tf))
                    first_listed[(symbol, tf)] = min(listed) if listed else None
                    available[(symbol, tf)] = set(months) & listed
                    print(f"[DISCOVER] {symbol} {tf}: months={len(available[(symbol, tf)])} "
                          f"first={min(listed) if listed else '-'} "
                          f"last={max(listed) if listed else '-'}", flush=True)
        except Exception as exc:
            report["discovery"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
            report["errors"].append(f"discovery: {type(exc).__name__}: {exc}")
            print("[ERROR] Discovery unavailable. Use --category ... --check-only "
                  "for documented URL HEAD probing.", file=sys.stderr)
            datadir = args.datadir.resolve()
            datadir.mkdir(parents=True, exist_ok=True)
            path = datadir / "vision_import_report.json"
            path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return 1

    datadir = args.datadir.resolve()
    for symbol in symbols:
        for tf in args.timeframes:
            frames, missing_months = [], []
            pair_has_error = False
            # With automatic discovery and no explicit start, avoid treating all
            # months before the first published archive as missing history.
            pair_months = months
            if args.discover and args.start is None:
                first = first_listed.get((symbol, tf))
                pair_months = [m for m in months if first is not None and m >= first]
            for month in pair_months:
                url, name = archive_url(symbol, tf, month)
                item = {"symbol": symbol, "timeframe": tf, "month": month, "url": url}
                try:
                    if args.discover and month not in available.get((symbol, tf), set()):
                        item["status"] = "NOT_LISTED"
                        missing_months.append(month)
                        print(f"[SKIP]  {symbol} {tf} {month}: not listed", flush=True)
                    elif args.check_only:
                        if args.discover:
                            item["status"] = "LISTED"
                        else:
                            status, _ = fetch(url, method="HEAD")
                            item["status"] = f"HTTP_{status}"
                            if status not in (200, 404):
                                pair_has_error = True
                                report["errors"].append(f"{name}: HTTP_{status}")
                        print(f"[CHECK] {symbol} {tf} {month}: {item['status']}", flush=True)
                    else:
                        status, path, expected = download_archive(
                            url=url, name=name,
                            raw_dir=datadir / "raw_vision" / symbol / tf)
                        item["status"] = status
                        if path:
                            frame = parse_kline_zip(path, month=month, timeframe=tf)
                            item.update({"rows": len(frame), "zip_sha256": expected,
                                         "first_open": frame.date.iloc[0].isoformat(),
                                         "last_open": frame.date.iloc[-1].isoformat()})
                            frames.append(frame)
                        else:
                            missing_months.append(month)
                            if (status.startswith("archive_http_") or status.startswith("checksum_http_")
                                    or status == "missing_checksum"
                                    or (args.discover and status == "missing_archive")):
                                pair_has_error = True
                                report["errors"].append(f"{name}: {status}")
                        print(f"[ZIP]   {symbol} {tf} {month}: {status}"
                              + (f", rows={len(frame)}" if path else ""), flush=True)
                except Exception as exc:
                    item["status"] = "ERROR"
                    item["error"] = f"{type(exc).__name__}: {exc}"
                    report["errors"].append(f"{name}: {item['error']}")
                    pair_has_error = True
                    missing_months.append(month)
                    print(f"[ERROR] {name}: {exc}", file=sys.stderr, flush=True)
                report["archives"].append(item)
            if args.check_only:
                continue
            if pair_has_error:
                report["datasets"].append({"symbol": symbol, "timeframe": tf,
                                           "status": "BLOCKED_ON_ERROR",
                                           "missing_months": missing_months})
                continue
            if not frames:
                report["datasets"].append({"symbol": symbol, "timeframe": tf,
                                           "status": "NO_DATA", "missing_months": missing_months})
                continue
            try:
                import pandas as pd
                frame = pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)
                if frame.date.duplicated().any():
                    raise ValueError("duplicate open timestamps across monthly archives")
                gaps = gaps_summary(frame, tf)
                path, action = export_feather(frame, symbol=symbol, timeframe=tf,
                                              datadir=datadir, overwrite=args.overwrite)
                detail = {
                    "symbol": symbol, "timeframe": tf, "rows": len(frame),
                    "first_open": frame.date.iloc[0].isoformat(),
                    "last_open": frame.date.iloc[-1].isoformat(),
                    "feather_path": str(path), "feather_sha256": sha256_file(path),
                    "status": "PARTIAL" if missing_months else action,
                    "file_action": action, "missing_months": missing_months, "gaps": gaps,
                    "warning": "Only traded-price OHLCV; mark/index/funding absent. Futures backtest NOT ready."
                }
                report["datasets"].append(detail)
                print(f"[SAVE]  {path} rows={len(frame)} gaps={gaps['gap_intervals']} ({action})", flush=True)
            except Exception as exc:
                report["errors"].append(f"{symbol}/{tf} export: {type(exc).__name__}: {exc}")
                print(f"[ERROR] {symbol}/{tf} export: {exc}", file=sys.stderr, flush=True)

    datadir.mkdir(parents=True, exist_ok=True)
    report_path = datadir / "vision_import_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nReport: {report_path}")
    exported = sum("feather_path" in item for item in report["datasets"])
    partial = sum(item.get("status") == "PARTIAL" for item in report["datasets"])
    absent = sum(item.get("status") == "NO_DATA" for item in report["datasets"])
    print(f"Archives: {len(report['archives'])}, exported: {exported}, partial: {partial}, "
          f"no-data: {absent}, errors: {len(report['errors'])}")
    if report["errors"] or (not args.check_only and exported == 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
