"""Immutable Parquet object publication and Catalog v3 registration."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pandas as pd
import pytest

from data_engineering.catalog_v3 import get_coverage, list_datasets, validate_dataset
from data_engineering.sources.contracts import DatasetIdentity, ObservationBatch, SourceArtifact
from data_engineering.sources.parquet import (
    PublishStatus,
    provenance_key,
    publish,
)
from data_engineering.sources.pipeline import validate


IDENTITY = DatasetIdentity(
    "binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "5m"
)


def _batch(month, *, request_id=None, raw_source=b"source", end=None):
    start = datetime.fromisoformat(f"{month}-01").replace(tzinfo=timezone.utc)
    year, month_number = start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1
    month_end = datetime(year, month_number, 1, tzinfo=timezone.utc)
    end = end or month_end
    dates = pd.date_range(start, end, freq="5min", inclusive="left")
    frame = pd.DataFrame({
        "date": dates,
        "open": [100.0] * len(dates), "high": [101.0] * len(dates),
        "low": [99.0] * len(dates), "close": [100.5] * len(dates),
        "volume": [2.0] * len(dates),
    })
    artifact = SourceArtifact(
        identity=IDENTITY,
        request_id=request_id or f"request-{month}",
        source_locator=f"binance-local:BTC/{month}.feather",
        source_format="local-feather",
        start=start,
        end=end,
        availability="AVAILABLE",
        metadata={"adapter": "freqtrade-feather"},
    )
    return ObservationBatch(
        identity=IDENTITY,
        artifact=artifact,
        observations=frame,
        source_sha256=hashlib.sha256(raw_source).hexdigest(),
        metadata={"adapter": "freqtrade-feather", "source_locator": artifact.source_locator},
    )


def _validated(batch, *, allow_partial=False):
    from data_engineering.sources.pipeline import ValidationPolicy

    return validate(
        batch,
        converter_version="parquet-converter-v1",
        policy=ValidationPolicy(allow_partial=allow_partial),
    )


def _roots(tmp_path):
    normalized = tmp_path / "normalized"
    normalized.mkdir(exist_ok=True)
    return {"alpha-lab-normalized": normalized}


def test_publish_creates_immutable_parquet_and_is_idempotent(tmp_path):
    batch = _batch("2025-01")
    receipt = _validated(batch)
    roots = _roots(tmp_path)
    catalog_root = tmp_path / "catalog"
    result = publish(receipt, batch, roots, catalog_root)
    assert result.status is PublishStatus.PUBLISHED
    assert result.object_path.is_file()
    assert hashlib.sha256(result.object_path.read_bytes()).hexdigest() == receipt.normalized_sha256
    assert result.partition["storage_format"] == "parquet"
    assert result.partition["provenance"]["raw_source_sha256"] == receipt.source_sha256

    repeated = publish(receipt, batch, roots, catalog_root)
    assert repeated.status is PublishStatus.ALREADY_PRESENT
    assert repeated.object_path == result.object_path
    records = list_datasets(catalog_root)
    assert len(records) == 1
    assert validate_dataset(
        catalog_root, IDENTITY.dataset_id(), source_roots=roots
    )["status"] == "AVAILABLE"
    coverage = get_coverage(
        catalog_root, IDENTITY.dataset_id(), batch.artifact.start, batch.artifact.end,
        source_roots=roots,
    )
    assert coverage["status"] == "AVAILABLE"
    assert coverage["observed_count"] == receipt.row_count


def test_publish_creates_complete_append_only_snapshots(tmp_path):
    roots = _roots(tmp_path)
    catalog_root = tmp_path / "catalog"
    january = publish(_validated(_batch("2025-01")), _batch("2025-01"), roots, catalog_root)
    february_batch = _batch("2025-02")
    february = publish(_validated(february_batch), february_batch, roots, catalog_root)
    records = list_datasets(catalog_root)
    assert len(records) == 2
    latest = next(
        record["manifest"] for record in records
        if record["manifest_sha256"] == february.manifest["manifest_sha256"]
    )
    assert len(latest["partitions"]) == 2
    assert latest["revision"]["previous_manifest_sha256"] == january.manifest["manifest_sha256"]
    assert february.partition["start"].startswith("2025-02")


def test_provenance_key_distinguishes_raw_source_and_converter_versions():
    batch = _batch("2025-01", raw_source=b"first raw source")
    receipt = _validated(batch)
    other_source = replace(receipt, source_sha256=hashlib.sha256(b"second raw source").hexdigest())
    other_converter = replace(receipt, converter_version="parquet-converter-v2")
    locator = batch.artifact.source_locator
    assert provenance_key(receipt, "freqtrade-feather", locator) != provenance_key(other_source, "freqtrade-feather", locator)
    assert provenance_key(receipt, "freqtrade-feather", locator) != provenance_key(other_converter, "freqtrade-feather", locator)
    assert provenance_key(receipt, "freqtrade-feather", locator) != provenance_key(receipt, "freqtrade-feather", "another-source")


def test_publish_rejects_receipt_tampering_and_partial_overlap(tmp_path):
    roots = _roots(tmp_path)
    catalog_root = tmp_path / "catalog"
    whole = _batch("2025-01")
    publish(_validated(whole), whole, roots, catalog_root)
    changed_receipt = replace(_validated(whole), normalized_sha256="0" * 64)
    with pytest.raises(ValueError, match="receipt"):
        publish(changed_receipt, whole, roots, catalog_root)

    partial_end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    partial = _batch("2025-01", request_id="partial-jan", raw_source=b"partial", end=partial_end)
    with pytest.raises(ValueError, match="overlap"):
        publish(_validated(partial, allow_partial=True), partial, roots, catalog_root)


def test_publish_replaces_only_exact_feather_month_in_new_snapshot(tmp_path):
    from data_engineering.catalog_v3 import register_dataset
    from data_engineering.freqtrade_feather import _frame_fingerprint

    source_root = tmp_path / "feather"
    source_root.mkdir()
    feather = source_root / "BTC_USDT_USDT-5m-futures.feather"
    dates = pd.date_range("2025-01-01", "2025-01-31 23:55", freq="5min", tz="UTC")
    full_frame = pd.DataFrame({
        "date": dates, "open": [100.0] * len(dates), "high": [101.0] * len(dates),
        "low": [99.0] * len(dates), "close": [100.5] * len(dates), "volume": [2.0] * len(dates),
    })
    full_frame.to_feather(feather)
    source_hash = hashlib.sha256(feather.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "data-catalog-v3", "dataset_id": IDENTITY.dataset_id(),
        "exchange": "binance", "market_type": "futures", "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT", "data_kind": "ohlcv", "timeframe": "5m",
        "source": "freqtrade-local", "source_reference": feather.name,
        "registered_at": "2026-10-20T00:00:00Z",
        "quality": {"status": "AVAILABLE", "issues": []},
        "partitions": [{
            "start": "2025-01-01T00:00:00Z", "end": "2025-02-01T00:00:00Z",
            "source_root_id": "binance-data", "source_path": feather.name,
            "first_observation": "2025-01-01T00:00:00Z", "last_observation": "2025-01-31T23:55:00Z",
            "row_count": len(full_frame), "source_sha256": source_hash,
            "fingerprint": _frame_fingerprint(full_frame), "quality_status": "AVAILABLE",
        }],
        "revision": {"previous_manifest_sha256": None, "reason": "initial registration"},
    }
    catalog_root = tmp_path / "catalog"
    register_dataset(catalog_root, manifest)
    roots = {**_roots(tmp_path), "binance-data": source_root}

    batch = _batch("2025-01")
    result = publish(_validated(batch), batch, roots, catalog_root)
    assert result.status is PublishStatus.PUBLISHED
    latest = result.manifest["manifest"]
    assert len(latest["partitions"]) == 1
    assert latest["partitions"][0]["storage_format"] == "parquet"


def test_publish_recovers_catalog_registration_after_object_commit_failure(tmp_path, monkeypatch):
    import data_engineering.sources.parquet as parquet_module

    roots = _roots(tmp_path)
    catalog_root = tmp_path / "catalog"
    batch = _batch("2025-01")
    receipt = _validated(batch)
    register = parquet_module.register_dataset

    def fail_registration(*args, **kwargs):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(parquet_module, "register_dataset", fail_registration)
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        publish(receipt, batch, roots, catalog_root)
    assert list((roots["alpha-lab-normalized"] / IDENTITY.dataset_id()).rglob("*.parquet"))

    monkeypatch.setattr(parquet_module, "register_dataset", register)
    recovered = publish(receipt, batch, roots, catalog_root)
    assert recovered.status is PublishStatus.PUBLISHED
    assert validate_dataset(
        catalog_root, IDENTITY.dataset_id(), source_roots=roots
    )["status"] == "AVAILABLE"


def test_publish_rejects_symlinked_normalized_root(tmp_path):
    target = tmp_path / "real-normalized"
    target.mkdir()
    linked = tmp_path / "linked-normalized"
    linked.symlink_to(target, target_is_directory=True)
    batch = _batch("2025-01")
    with pytest.raises(ValueError, match="symlink"):
        publish(
            _validated(batch), batch, {"alpha-lab-normalized": linked}, tmp_path / "catalog"
        )
