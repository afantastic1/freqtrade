"""Trusted point-in-time data selection for isolated Worker inputs."""

from __future__ import annotations

import math
from typing import Sequence

import pandas as pd

ALLOWED_FEATURES = frozenset({"open", "high", "low", "close", "volume"})
WORKER_ROW_FIELDS = frozenset({"open_time", "symbol", "available_time", *ALLOWED_FEATURES})


def build_asof_frame(panel: pd.DataFrame, *, as_of, symbols: Sequence[str],
                     required_columns: Sequence[str]) -> pd.DataFrame:
    """Return a strictly aligned, allowlisted history available by the cutoff."""
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    cutoff = cutoff.tz_convert("UTC")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be non-empty and unique")
    if not required_columns or not set(required_columns).issubset(ALLOWED_FEATURES):
        raise ValueError("required columns must be allowlisted market features")
    if isinstance(panel.index, pd.MultiIndex) and list(panel.index.names) == ["open_time", "symbol"]:
        if not panel.index.is_unique:
            raise ValueError("panel index must be unique")
        frame = panel.reset_index()
    elif {"open_time", "symbol"}.issubset(panel.columns):
        frame = panel.copy()
    else:
        raise ValueError("panel requires open_time and symbol keys")
    required = {"open_time", "symbol", "available_time", *required_columns}
    if not required.issubset(frame.columns):
        raise ValueError(f"panel missing required fields: {sorted(required - set(frame.columns))}")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    frame["available_time"] = pd.to_datetime(frame["available_time"], utc=True, errors="raise")
    if frame[["open_time", "symbol"]].duplicated().any():
        raise ValueError("panel rows must be unique")
    if ((frame["open_time"] > cutoff) & frame["available_time"].le(cutoff)).any():
        raise ValueError("panel has a future open_time marked available")
    frame = frame.loc[frame["symbol"].isin(symbols) & frame["available_time"].le(cutoff)].copy()
    if frame.empty:
        raise ValueError("no rows available by as_of")
    if not frame["available_time"].le(cutoff).all():
        raise ValueError("panel includes rows unavailable at as_of")
    for timestamp, section in frame.groupby("open_time", sort=True):
        if set(section["symbol"]) != set(symbols) or len(section) != len(symbols):
            raise ValueError(f"symbols are missing or shifted at {timestamp.isoformat()}")
    output_columns = ["open_time", "symbol", "available_time", *required_columns]
    output = frame[output_columns].sort_values(["open_time", "symbol"], kind="mergesort")
    output = output.set_index(["open_time", "symbol"])
    if not output.index.is_unique or not output.index.is_monotonic_increasing:
        raise ValueError("Worker input index must be sorted and unique")
    return output


def validate_worker_steps(steps: list[dict], *, symbols: Sequence[str], start, end,
                          max_lookback: int) -> list[dict]:
    """Fail closed before Broker transmission if any Worker frame is not PIT-safe."""
    start_time = pd.Timestamp(start)
    end_time = pd.Timestamp(end)
    if start_time.tzinfo is None or end_time.tzinfo is None or start_time >= end_time:
        raise ValueError("research window must be valid and timezone-aware")
    start_time, end_time = start_time.tz_convert("UTC"), end_time.tz_convert("UTC")
    if type(max_lookback) is not int or not 1 <= max_lookback <= 10000:
        raise ValueError("invalid lookback bound")
    if not steps or len(steps) > 512 or not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("invalid bounded step sequence")
    previous_as_of = None
    for step in steps:
        if set(step) != {"type", "as_of", "history"} or step["type"] != "step":
            raise ValueError("invalid worker step contract")
        as_of = pd.Timestamp(step["as_of"])
        if as_of.tzinfo is None:
            raise ValueError("step as_of must be timezone-aware")
        as_of = as_of.tz_convert("UTC")
        if not start_time <= as_of <= end_time or (previous_as_of is not None and as_of <= previous_as_of):
            raise ValueError("step as_of is outside or unordered within research window")
        previous_as_of = as_of
        history = step["history"]
        if not isinstance(history, list) or not history or len(history) > max_lookback * len(symbols):
            raise ValueError("step history exceeds lookback bound")
        seen = set()
        section_symbols: dict[pd.Timestamp, set[str]] = {}
        for row in history:
            if not isinstance(row, dict) or not set(row).issubset(WORKER_ROW_FIELDS):
                raise ValueError("Worker rows must contain only allowlisted feature fields")
            if not {"open_time", "symbol", "available_time"}.issubset(row):
                raise ValueError("Worker row is missing time or symbol metadata")
            for column in set(row) & ALLOWED_FEATURES:
                value = row[column]
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                ):
                    raise ValueError("Worker feature values must be finite numeric scalars or null")
            open_time = pd.Timestamp(row["open_time"])
            available_time = pd.Timestamp(row["available_time"])
            if open_time.tzinfo is None or available_time.tzinfo is None:
                raise ValueError("Worker row timestamps must be timezone-aware")
            open_time, available_time = open_time.tz_convert("UTC"), available_time.tz_convert("UTC")
            if open_time > as_of or available_time > as_of or available_time < open_time:
                raise ValueError("Worker row violates point-in-time cutoff")
            symbol = row["symbol"]
            if symbol not in symbols or (open_time, symbol) in seen:
                raise ValueError("Worker rows contain an undeclared or duplicate symbol")
            seen.add((open_time, symbol))
            section_symbols.setdefault(open_time, set()).add(symbol)
        if len(section_symbols) > max_lookback:
            raise ValueError("step history exceeds lookback bound")
        if any(assets != set(symbols) for assets in section_symbols.values()):
            raise ValueError("Worker history is not cross-asset time aligned")
        row_order = [(pd.Timestamp(row["open_time"]).tz_convert("UTC"), row["symbol"]) for row in history]
        if row_order != sorted(row_order):
            raise ValueError("Worker history must be sorted")
    return steps
