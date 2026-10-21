from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from alpha_lab_service.catalog_gateway_v2 import CatalogCoverageGatewayV2
from data_engineering.freqtrade_feather import register_feather_file


DATASET_KEY = "binance:futures:BTC:ohlcv:5m"


def _frame(dates):
    return pd.DataFrame({
        "date": pd.to_datetime(dates, utc=True),
        "open": [100.0] * len(dates),
        "high": [101.0] * len(dates),
        "low": [99.0] * len(dates),
        "close": [100.5] * len(dates),
        "volume": [2.0] * len(dates),
    })


def _registered_dataset(tmp_path, dates):
    source_root = tmp_path / "futures"
    source_root.mkdir()
    source = source_root / "BTC_USDT_USDT-5m-futures.feather"
    _frame(dates).to_feather(source)
    registered = register_feather_file(
        tmp_path / "catalog-root",
        source_root_id="binance-futures",
        source_root=source_root,
        relative_path=Path(source.name),
        exchange="binance",
        market_type="futures",
        asset="BTC",
        exchange_symbol="BTC/USDT:USDT",
        data_kind="ohlcv",
        timeframe="5m",
        source="test-fixture",
    )
    return registered["manifest"]["dataset_id"], tmp_path / "catalog-root", source_root


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _gateway(catalog_root, source_roots, dataset_id):
    return CatalogCoverageGatewayV2(
        catalog_root,
        source_roots,
        {DATASET_KEY: dataset_id} if dataset_id is not None else {},
    )


def test_path_like_dataset_alias_is_rejected_without_echoing_it(tmp_path):
    gateway = _gateway(tmp_path / "catalog-root", {}, None)

    result = gateway.get_data_coverage(
        dataset_key="../../etc/passwd",
        start="2025-01-01T00:00:00Z",
        end="2025-01-02T00:00:00Z",
    )

    assert result == {"status": "INVALID_REQUEST", "error_code": "INVALID_DATASET_KEY"}
    assert "/etc/passwd" not in str(result)


def test_unknown_dataset_alias_is_not_configured(tmp_path):
    gateway = _gateway(tmp_path / "catalog-root", {}, None)

    result = gateway.get_data_coverage(
        dataset_key="binance:futures:ETH:ohlcv:5m",
        start="2025-01-01T00:00:00Z",
        end="2025-01-02T00:00:00Z",
    )

    assert result == {
        "status": "NOT_CONFIGURED",
        "error_code": "DATASET_NOT_CONFIGURED",
    }


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-01-01", "2025-01-02T00:00:00Z"),
        ("2025-01-02T00:00:00Z", "2025-01-01T00:00:00Z"),
        ("2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    ],
)
def test_invalid_or_reversed_interval_is_rejected_without_catalog_access(tmp_path, start, end):
    gateway = _gateway(tmp_path / "not-created", {}, "dataset-id")

    result = gateway.get_data_coverage(dataset_key=DATASET_KEY, start=start, end=end)

    assert result["status"] == "INVALID_REQUEST"
    assert result["error_code"] == "INVALID_INTERVAL"
    assert not (tmp_path / "not-created").exists()


def test_missing_dataset_returns_catalog_missing_status(tmp_path):
    gateway = _gateway(tmp_path / "catalog-root", {}, "not-registered")

    result = gateway.get_data_coverage(
        dataset_key=DATASET_KEY,
        start="2025-01-01T00:00:00Z",
        end="2025-01-02T00:00:00Z",
    )

    assert result["status"] == "MISSING"
    assert result["observed_count"] == 0
    assert result["dataset_id"] == "not-registered"


def test_partial_coverage_is_returned_without_mutating_catalog_or_source(tmp_path):
    dataset_id, catalog_root, source_root = _registered_dataset(
        tmp_path,
        ["2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z"],
    )
    catalog_before = _tree_digest(catalog_root)
    source_before = _tree_digest(source_root)
    gateway = _gateway(catalog_root, {"binance-futures": source_root}, dataset_id)

    result = gateway.get_data_coverage(
        dataset_key=DATASET_KEY,
        start="2025-01-01T00:00:00Z",
        end="2025-01-01T00:15:00Z",
    )

    assert result["status"] == "PARTIAL"
    assert result["observed_count"] == 2
    assert result["missing_observations"] == 1
    assert result["missing_intervals"] == [
        {"start": "2025-01-01T00:05:00Z", "end": "2025-01-01T00:10:00Z"}
    ]
    assert _tree_digest(catalog_root) == catalog_before
    assert _tree_digest(source_root) == source_before


def test_unconfigured_source_root_is_reported_unavailable_without_path_details(tmp_path):
    dataset_id, catalog_root, _source_root = _registered_dataset(
        tmp_path, ["2025-01-01T00:00:00Z"]
    )
    gateway = _gateway(catalog_root, {}, dataset_id)

    result = gateway.get_data_coverage(
        dataset_key=DATASET_KEY,
        start="2025-01-01T00:00:00Z",
        end="2025-01-01T00:05:00Z",
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["error_code"] == "SOURCE_ROOT_MISSING"
    assert str(tmp_path) not in str(result)
