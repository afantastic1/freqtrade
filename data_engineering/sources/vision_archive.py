"""Reusable parsing, checksum, and bounded metadata logic for Vision archives."""

from __future__ import annotations

import re
from pathlib import Path
import time
from typing import Callable
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile


ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
S3_BUCKET_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_ROOT_PREFIX = "data/futures/um/monthly/klines/"
TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
}
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
)
MONTH_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")


def s3_list_prefixes(
    prefix: str,
    *,
    fetcher: Callable,
    max_pages: int = 8,
    sleep=time.sleep,
) -> list[str]:
    """Read a bounded public S3 listing and fail closed on incomplete pages."""
    seen = set()
    marker = ""
    for page in range(max_pages):
        params = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
        if marker:
            params["marker"] = marker
        url = S3_BUCKET_LIST + "?" + urllib.parse.urlencode(params)
        status, body = fetcher(url, method="GET", retries=1, timeout=30)
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
        prefixes = [node.text for node in root.findall(".//{*}CommonPrefixes/{*}Prefix") if node.text]
        keys = [node.text for node in root.findall(".//{*}Contents/{*}Key") if node.text]
        for item in prefixes + keys:
            if not item.startswith(prefix):
                raise ValueError("S3 listing returned a key outside requested prefix")
            seen.add(item)
        if (root.findtext("{*}IsTruncated") or "").lower() != "true":
            return sorted(seen)
        next_marker = root.findtext("{*}NextMarker")
        if not next_marker:
            next_marker = (prefixes + keys)[-1] if prefixes or keys else ""
        if not next_marker or next_marker <= marker:
            raise ValueError("S3 listing pagination did not advance")
        marker = next_marker
        if page + 1 < max_pages:
            sleep(0.3)
    raise ValueError("S3 listing exceeded page limit; refusing incomplete discovery")


def discover_archive_months(symbol: str, timeframe: str, *, fetcher: Callable) -> list[str]:
    prefix = f"{S3_ROOT_PREFIX}{symbol}/{timeframe}/"
    entries = s3_list_prefixes(prefix, fetcher=fetcher)
    return months_from_entries(symbol, timeframe, entries)


def months_from_entries(symbol: str, timeframe: str, entries: list[str]) -> list[str]:
    prefix = f"{S3_ROOT_PREFIX}{symbol}/{timeframe}/"
    expected_prefix = f"{prefix}{symbol}-{timeframe}-"
    pattern = re.compile(rf"{re.escape(expected_prefix)}(\d{{4}}-\d{{2}})\.zip\Z")
    months = set()
    for entry in entries:
        match = pattern.fullmatch(entry)
        if match and MONTH_PATTERN.fullmatch(match.group(1)):
            months.add(match.group(1))
    return sorted(months)


def archive_url(symbol: str, timeframe: str, month: str) -> tuple[str, str]:
    if timeframe not in TIMEFRAME_SECONDS or not MONTH_PATTERN.fullmatch(month):
        raise ValueError("unsupported Vision timeframe or UTC month")
    name = f"{symbol}-{timeframe}-{month}.zip"
    return f"{ARCHIVE_BASE}/{symbol}/{timeframe}/{name}", name


def checksum_expected(checksum: bytes, archive_name: str) -> str:
    content = checksum.decode("utf-8-sig", errors="strict").strip()
    for line in content.splitlines():
        match = re.fullmatch(r"([A-Fa-f0-9]{64})\s+\*?(.+)", line.strip())
        if match and Path(match.group(2)).name == archive_name:
            return match.group(1).lower()
    raise ValueError(f"bad checksum file, missing expected {archive_name}")


def parse_kline_zip(zip_path: Path, *, month: str, timeframe: str):
    """Parse one monthly USD-M kline ZIP without sorting or filling observations."""
    import numpy as np
    import pandas as pd

    if timeframe not in TIMEFRAME_SECONDS or not MONTH_PATTERN.fullmatch(month):
        raise ValueError("unsupported Vision timeframe or UTC month")
    with zipfile.ZipFile(zip_path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir() and item.filename.endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected exactly one CSV in {zip_path.name}, got {len(members)}")
        member = members[0]
        if member.file_size > 120 * 1024 * 1024:
            raise ValueError("oversized uncompressed CSV")
        with archive.open(member) as stream:
            frame = pd.read_csv(stream, header=None, dtype=str)
    if frame.shape[1] != len(COLUMNS):
        raise ValueError(f"unexpected CSV column count {frame.shape[1]}; expected 12")
    frame.columns = list(COLUMNS)
    if len(frame) and str(frame.iloc[0]["open_time"]).strip().lower() in ("open_time", "open time"):
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.empty:
        raise ValueError("empty CSV")
    ticks = pd.to_numeric(frame["open_time"], errors="raise")
    if ticks.isna().any() or not np.isfinite(ticks).all():
        raise ValueError("invalid open_time")
    unit = "us" if float(ticks.median()) >= 1e14 else "ms"
    dates = pd.to_datetime(ticks, unit=unit, utc=True, errors="raise")
    start = pd.Timestamp(month + "-01", tz="UTC")
    year, month_number = map(int, month.split("-"))
    end = pd.Timestamp(year=year + (month_number == 12), month=1 if month_number == 12 else month_number + 1,
                       day=1, tz="UTC")
    if bool(((dates < start) | (dates >= end)).any()):
        raise ValueError("archive has K-line open times outside requested calendar month")
    if bool(dates.duplicated().any()) or not dates.is_monotonic_increasing:
        raise ValueError("duplicate or out-of-order K-line open times")
    # pandas may infer datetime64[ms] (notably with pandas 3), so normalize
    # before interpreting the integer representation as nanoseconds.
    ticks_ns = dates.dt.as_unit("ns").astype("int64")
    if bool((ticks_ns % (TIMEFRAME_SECONDS[timeframe] * 1_000_000_000) != 0).any()):
        raise ValueError("misaligned K-line timestamps")
    result = pd.DataFrame({"date": dates})
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(frame[column], errors="raise").astype("float64").to_numpy()
    values = result[["open", "high", "low", "close", "volume"]].to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("NaN or infinite OHLCV")
    if bool((result[["open", "high", "low", "close"]] <= 0).any().any()):
        raise ValueError("nonpositive OHLC price")
    if bool((result["volume"] < 0).any()):
        raise ValueError("negative volume")
    if bool(((result["high"] < result[["open", "close", "low"]].max(axis=1)) |
             (result["low"] > result[["open", "close", "high"]].min(axis=1))).any()):
        raise ValueError("inconsistent OHLC ordering")
    return result[["date", "open", "high", "low", "close", "volume"]]
