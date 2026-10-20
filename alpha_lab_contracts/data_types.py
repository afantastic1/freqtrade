"""Stable identifiers for data sources accepted by contract protocols."""

DATA_TYPE_IDS = frozenset({"futures_ohlcv", "spot_ohlcv", "funding_rate", "open_interest"})


def is_data_type(value: str) -> bool:
    return isinstance(value, str) and value in DATA_TYPE_IDS
