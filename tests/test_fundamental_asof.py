import pandas as pd
import pytest

from alpha_lab.data.market_panel import MarketPanel
from data_engineering.fundamentals import (
    OUTPUT_COLUMNS, _STRICT_PIT_READ_TOKEN, _frame_fingerprint, asof_join,
)


def _panel(symbol="BTC/USDT:USDT", decisions=None):
    decisions = decisions or ["2024-01-01T10:00:00Z", "2024-01-01T12:00:00Z",
                              "2024-01-01T13:00:00Z", "2024-01-01T15:00:00Z"]
    times = pd.to_datetime(decisions, utc=True)
    index = pd.MultiIndex.from_arrays(
        [times, [symbol] * len(times)], names=["open_time", "symbol"]
    )
    frame = pd.DataFrame({
        "available_time": times,
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
    }, index=index)
    return MarketPanel(frame)


def _observations(rows):
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for column in OUTPUT_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame["pit_status"] = frame.pit_status.str.upper()
    frame["event_time"] = pd.to_datetime(frame.event_time, utc=True)
    frame["available_time"] = pd.to_datetime(frame.available_time, utc=True)
    frame["available_at"] = frame.available_time
    frame["ingested_at"] = pd.Timestamp("2024-12-31T00:00:00Z")
    frame["evidence_sha256s"] = [[] for _ in range(len(frame))]
    frame.attrs.update(access_mode="strict_pit", _strict_pit_read_token=_STRICT_PIT_READ_TOKEN)
    frame.attrs["_strict_pit_read_fingerprint"] = _frame_fingerprint(frame)
    return frame


def _obs(available, value, *, asset="token:BTC", tradable="BTC", status="verified"):
    return {
        "asset_id": asset, "tradable_asset_id": tradable, "entity_scope": "token",
        "metric": "CapMrktEstUSD", "event_time": "2024-01-01T00:00:00Z",
        "available_time": available, "value": value, "source": "coinmetrics-community",
        "dataset_id": "coinmetrics_btc_dev_v1", "snapshot_sha256": "a" * 64,
        "pit_status": status.upper(),
    }


def test_asof_join_uses_availability_not_event_time_and_allows_exact_time():
    observations = _observations([
        _obs("2024-01-01T11:00:00Z", 7.0),
        _obs("2024-01-01T13:00:00Z", 9.0),
    ])

    result = asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="3h")

    assert pd.isna(result.fundamental_value.iloc[0])
    assert result.fundamental_value.iloc[1] == 7.0
    assert result.fundamental_value.iloc[2] == 9.0
    assert result.fundamental_value.iloc[3] == 9.0
    assert result.pit_status.dropna().eq("VERIFIED").all()


def test_asof_join_rejects_unverified_or_missing_availability():
    for observation in (
        _obs("2024-01-01T09:00:00Z", 1, status="unverified"),
        _obs(None, 1),
    ):
        with pytest.raises(ValueError, match="requires verified availability"):
            asof_join(_panel(), _observations([observation]),
                      metric="CapMrktEstUSD", max_age="24h")


def test_conservative_upper_bound_prevents_join_before_proven_availability():
    upper_bound = pd.Timestamp("2024-01-01T13:00:00Z")
    observation = _obs(upper_bound, 5.0, status="CONSERVATIVE")
    observation["available_at"] = None
    observation["available_at_upper_bound"] = upper_bound
    observations = _observations([observation])

    result = asof_join(
        _panel(decisions=["2024-01-01T12:00:00Z", "2024-01-01T13:00:00Z"]),
        observations, metric="CapMrktEstUSD", max_age="24h",
    )

    assert pd.isna(result.fundamental_value.iloc[0])
    assert result.fundamental_value.iloc[1] == 5.0
    assert result.pit_status.iloc[1] == "CONSERVATIVE"


def test_asof_join_requires_adapter_strict_pit_read():
    observations = _observations([_obs("2024-01-01T09:00:00Z", 5.0)])
    observations.attrs = {"access_mode": "strict_pit"}

    with pytest.raises(ValueError, match="strict_pit mode"):
        asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="24h")


def test_asof_join_rejects_mutated_strict_frame():
    observations = _observations([_obs("2024-01-01T09:00:00Z", 5.0)])
    observations.loc[0, "value"] = 999.0

    with pytest.raises(ValueError, match="changed after adapter validation"):
        asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="24h")


def test_asof_join_rejects_changed_pin_metadata():
    observations = _observations([_obs("2024-01-01T09:00:00Z", 5.0)])
    observations.attrs["snapshot_pins"] = [("other", "f" * 64)]

    with pytest.raises(ValueError, match="changed after adapter validation"):
        asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="24h")


def test_asof_join_keeps_missing_asset_and_expired_values_null():
    observations = _observations([_obs("2024-01-01T09:00:00Z", 5.0)])
    btc = asof_join(_panel(decisions=["2024-01-01T15:00:00Z"]), observations,
                    metric="CapMrktEstUSD", max_age="2h")
    eth_panel = _panel(symbol="ETH/USDT:USDT", decisions=["2024-01-01T10:00:00Z"])
    eth = asof_join(eth_panel, observations, metric="CapMrktEstUSD", max_age="2h")

    assert pd.isna(btc.fundamental_value.iloc[0])
    assert pd.isna(eth.fundamental_value.iloc[0])


def test_asof_join_requires_explicit_nonnegative_max_age():
    observations = _observations([_obs("2024-01-01T09:00:00Z", 5.0)])
    with pytest.raises(ValueError, match="explicit positive duration"):
        asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="")
    with pytest.raises(ValueError, match="non-negative"):
        asof_join(_panel(), observations, metric="CapMrktEstUSD", max_age="-1h")


def test_asof_join_rejects_reserved_market_dates():
    observations = _observations([_obs("2024-12-30T00:00:00Z", 5.0)])

    with pytest.raises(ValueError, match="development window"):
        asof_join(
            _panel(decisions=["2025-01-01T00:00:00Z"]), observations,
            metric="CapMrktEstUSD", max_age="24h",
        )
