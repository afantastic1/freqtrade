"""Read-only behavior of the local Freqtrade Feather source adapter."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pandas as pd
import pytest

from data_engineering.sources.contracts import DatasetIdentity, DatasetRequest
from data_engineering.sources.freqtrade import FreqtradeFeatherAdapter


def _request(kind="funding_rate", market="futures", timeframe="1h", symbol=None):
    if symbol is None:
        symbol = "BTC/USDT:USDT" if market == "futures" else "BTC/USDT"
    identity = DatasetIdentity(
        exchange="binance", market_type=market, asset="BTC",
        exchange_symbol=symbol, data_kind=kind, timeframe=timeframe,
    )
    return DatasetRequest(
        identity, datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 3, 1, tzinfo=timezone.utc), "request-1",
    )


def _adapter(root, market="futures"):
    return FreqtradeFeatherAdapter({"binance": root}, exchange="binance", market_type=market)


def test_discover_and_read_returns_bounded_monthly_sparse_observations_in_source_order(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    path = root / "BTC_USDT_USDT-1h-funding_rate.feather"
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-02-10T00:00:00Z", "2025-01-20T00:00:00Z", "2025-02-01T00:00:00Z"],
                utc=True,
            ),
            "funding_rate": [0.02, 0.01, 0.03],
        }
    )
    frame.to_feather(path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    adapter = _adapter(root)
    artifacts = adapter.discover(_request())
    assert [(item.start.month, item.end.month) for item in artifacts] == [(1, 2), (2, 3)]
    assert [item.relative_path for item in artifacts] == [path.name, path.name]

    january = adapter.read(artifacts[0])
    february = adapter.read(artifacts[1])
    assert list(january.observations["date"].dt.month) == [1]
    assert list(february.observations["date"].dt.day) == [10, 1]
    assert list(february.observations["funding_rate"]) == [0.02, 0.03]
    assert january.source_sha256 == original_hash == february.source_sha256
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize(
    "market,filename,kind,symbol",
    [
        ("spot", "BTC_USDT-5m.feather", "ohlcv", "BTC/USDT"),
        ("futures", "BTC_USDT_USDT-5m-futures.feather", "ohlcv", "BTC/USDT:USDT"),
        ("futures", "BTC_USDT_USDT-1h-mark.feather", "mark_price", "BTC/USDT:USDT"),
        ("futures", "BTC_USDT_USDT-1h-open_interest.feather", "open_interest", "BTC/USDT:USDT"),
    ],
)
def test_discovery_covers_spot_futures_and_native_data_kinds(
    tmp_path, market, filename, kind, symbol
):
    root = tmp_path / "source"
    root.mkdir()
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
            "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [2.0],
            "open_interest_amount": [5.0],
        }
    )
    frame.to_feather(root / filename)
    request = _request(kind=kind, market=market, timeframe="5m" if kind == "ohlcv" else "1h", symbol=symbol)
    artifacts = _adapter(root, market).discover(request)
    assert len(artifacts) == 1
    batch = _adapter(root, market).read(artifacts[0])
    if kind == "mark_price":
        assert batch.observations.loc[0, "mark_price"] == 10.5


def test_read_preserves_duplicate_and_out_of_order_source_rows(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    path = root / "BTC_USDT_USDT-1h-funding_rate.feather"
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-01-02T00:00:00Z", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
                utc=True,
            ),
            "funding_rate": [0.02, 0.01, 0.03],
        }
    )
    frame.to_feather(path)
    adapter = _adapter(root)
    artifact = adapter.discover(_request())[0]
    batch = adapter.read(artifact)
    assert list(batch.observations["funding_rate"]) == [0.02, 0.01, 0.03]
    assert batch.observations["date"].is_monotonic_increasing is False
    assert batch.observations["date"].duplicated().any()


def test_adapter_rejects_symlink_roots_files_and_artifact_traversal(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    real = tmp_path / "real.feather"
    pd.DataFrame(
        {"date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True), "funding_rate": [0.01]}
    ).to_feather(real)
    (root / "BTC_USDT_USDT-1h-funding_rate.feather").symlink_to(real)
    adapter = _adapter(root)
    with pytest.raises(ValueError, match="symlink"):
        adapter.discover(_request())

    (root / "BTC_USDT_USDT-1h-funding_rate.feather").unlink()
    (root / "BTC_USDT_USDT-1h-funding_rate.feather").write_bytes(real.read_bytes())
    artifact = adapter.discover(_request())[0]
    with pytest.raises(ValueError, match="path"):
        adapter.read(replace(artifact, relative_path="../real.feather"))

    linked_root = tmp_path / "source-link"
    linked_root.symlink_to(root)
    with pytest.raises(ValueError, match="symlink"):
        FreqtradeFeatherAdapter({"binance": linked_root}, exchange="binance", market_type="futures").discover(_request())
