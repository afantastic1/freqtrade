"""Timeframe conversion used by Alpha Lab without importing the Freqtrade app."""

from __future__ import annotations


_SECONDS_PER_UNIT = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "M": 30 * 24 * 60 * 60,
    "y": 365 * 24 * 60 * 60,
}


def timeframe_to_seconds(timeframe: str) -> int:
    """Convert CCXT-style timeframe values using the existing fixed-unit rules."""
    amount = int(timeframe[:-1])
    unit = timeframe[-1]
    try:
        seconds_per_unit = _SECONDS_PER_UNIT[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe unit: {unit}") from exc
    return amount * seconds_per_unit
