"""Versioned contracts for data kinds represented by Data Catalog v3."""

from __future__ import annotations

DATA_KIND_CONTRACTS = {
    "ohlcv": {
        "required_fields": frozenset({"date", "open", "high", "low", "close", "volume"}),
        "one_of_fields": frozenset(),
        "timestamp_field": "date",
        "cadence_policy": "dense",
        "validators": (
            "finite_positive_prices",
            "nonnegative_volume",
            "ohlc_bounds",
            "unique_timestamps",
            "timeframe_alignment",
        ),
    },
    "funding_rate": {
        "required_fields": frozenset({"date", "funding_rate"}),
        "one_of_fields": frozenset(),
        "timestamp_field": "date",
        "cadence_policy": "event",
        "validators": ("finite_funding_rate", "unique_timestamps"),
    },
    "open_interest": {
        "required_fields": frozenset({"date"}),
        "one_of_fields": frozenset({"open_interest_amount", "open_interest_value"}),
        "timestamp_field": "date",
        "cadence_policy": "event",
        "validators": ("nonnegative_open_interest", "unique_timestamps"),
    },
    "mark_price": {
        "required_fields": frozenset({"date", "mark_price"}),
        "one_of_fields": frozenset(),
        "timestamp_field": "date",
        "cadence_policy": "dense",
        "validators": (
            "finite_positive_mark_price",
            "unique_timestamps",
            "timeframe_alignment",
        ),
    },
}
