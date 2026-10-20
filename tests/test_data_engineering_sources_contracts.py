"""Shared contracts for normalized data source adapters."""

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from data_engineering.sources.contracts import (
    AdapterCapability,
    DatasetIdentity,
    DatasetRequest,
    DownloadResult,
    DownloadStatus,
    ObservationBatch,
    SourceArtifact,
    UnsupportedCapabilityError,
    ValidationReceipt,
    download_artifact,
)


def _identity(**changes) -> DatasetIdentity:
    fields = {
        "exchange": "binance",
        "market_type": "futures",
        "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT",
        "data_kind": "ohlcv",
        "timeframe": "5m",
    }
    fields.update(changes)
    return DatasetIdentity(**fields)


def test_dataset_id_preserves_pr02_symbol_sensitive_identity():
    identity = _identity()
    symbol_hash = hashlib.sha256(b"BTC/USDT:USDT").hexdigest()[:12]
    assert identity.dataset_id() == f"binance-futures-btc-ohlcv-5m-{symbol_hash}"


def test_dataset_identity_changes_for_symbol_kind_or_schema():
    base_id = _identity().dataset_id()
    assert _identity(exchange_symbol="BTC/USDC:USDC").dataset_id() != base_id
    assert _identity(data_kind="mark_price").dataset_id() != base_id
    assert _identity(schema_version="alpha-observation-v2").dataset_id() != base_id


def test_request_range_and_request_id_do_not_change_dataset_identity():
    identity = _identity()
    first = DatasetRequest(
        identity, datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 2, 1, tzinfo=timezone.utc), "request-a",
    )
    second = DatasetRequest(
        identity, datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc), "request-b",
    )
    assert first.identity.dataset_id() == second.identity.dataset_id()
    assert first.request_id != second.request_id


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2025, 1, 1), datetime(2025, 2, 1, tzinfo=timezone.utc)),
        (datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=1))),
         datetime(2025, 2, 1, tzinfo=timezone.utc)),
        (datetime(2025, 2, 1, tzinfo=timezone.utc),
         datetime(2025, 1, 1, tzinfo=timezone.utc)),
    ],
)
def test_request_rejects_naive_non_utc_or_reversed_bounds(start, end):
    with pytest.raises(ValueError):
        DatasetRequest(_identity(), start, end, "request")


def test_source_artifact_and_validation_receipt_bind_identity_and_digests():
    identity = _identity()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 2, 1, tzinfo=timezone.utc)
    artifact = SourceArtifact(
        identity=identity,
        request_id="request-1",
        source_locator="BTC/USDT/5m/2025-01.zip",
        source_format="zip-csv",
        start=start,
        end=end,
        availability="AVAILABLE",
        expected_checksum="a" * 64,
    )
    batch = ObservationBatch(
        identity=identity,
        artifact=artifact,
        observations=object(),
        source_sha256="b" * 64,
    )
    assert batch.artifact is artifact
    receipt = ValidationReceipt(
        dataset_id=identity.dataset_id(),
        request_id="request-1",
        source_sha256=batch.source_sha256,
        normalized_sha256="c" * 64,
        converter_version="1",
        schema_version=identity.schema_version,
        start=start,
        end=end,
        validation_fingerprint="d" * 64,
    )
    assert receipt.source_sha256 == batch.source_sha256
    assert receipt.start == start and receipt.end == end


def test_download_result_models_explicit_statuses():
    result = DownloadResult(DownloadStatus.CHECKSUM_FAILED, issue_code="CHECKSUM_MISMATCH")
    assert result.status is DownloadStatus.CHECKSUM_FAILED
    assert result.issue_code == "CHECKSUM_MISMATCH"


def test_download_requires_declared_capability():
    class LocalOnlyAdapter:
        capabilities = frozenset({AdapterCapability.READ})

        def download(self, artifact, destination):  # pragma: no cover - must not be called
            raise AssertionError("unsupported download must be rejected before dispatch")

    with pytest.raises(UnsupportedCapabilityError):
        download_artifact(LocalOnlyAdapter(), object(), "unused")
