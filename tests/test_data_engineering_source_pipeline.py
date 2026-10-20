"""Staged discovery, validation, and recovery semantics for sources."""

from datetime import datetime, timezone
from dataclasses import replace
import hashlib

import pandas as pd
import pytest

from data_engineering.sources.contracts import (
    AvailabilityStatus, DatasetIdentity, DatasetRequest, ObservationBatch, SourceArtifact,
)
from data_engineering.sources.pipeline import (
    JournalState,
    JournalStore,
    SourceValidationError,
    ValidationPolicy,
    plan,
    validate,
)


def _request(kind="ohlcv", start="2025-01-01", end="2025-03-01"):
    identity = DatasetIdentity(
        "binance", "futures", "BTC", "BTC/USDT:USDT", kind,
        "5m" if kind == "ohlcv" else "1h",
    )
    return DatasetRequest(
        identity, datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc), "pipeline-request",
    )


def _artifact(request, month, availability=AvailabilityStatus.AVAILABLE, source_format="local-feather"):
    start = datetime.fromisoformat(f"{month}-01").replace(tzinfo=timezone.utc)
    year, month_number = start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1
    end = start.replace(year=year, month=month_number)
    return SourceArtifact(
        identity=request.identity, request_id=request.request_id,
        source_locator=f"local:{month}", source_format=source_format,
        start=start, end=end, availability=availability,
    )


class FakeAdapter:
    name = "fake"
    capabilities = frozenset({"discover", "read"})

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.download_calls = 0
        self.read_calls = 0

    def discover(self, request):
        return self.artifacts

    def read(self, artifact):
        self.read_calls += 1
        raise AssertionError("planning must not read source payloads")

    def download(self, artifact, destination):
        self.download_calls += 1
        raise AssertionError("planning must not download payloads")


class FakeCatalog:
    def __init__(self, status="MISSING", partitions=()):
        self.status = status
        self.partitions = list(partitions)

    def inspect(self, identity):
        return {
            "dataset_id": identity.dataset_id(),
            "status": self.status,
            "manifest": {"partitions": self.partitions},
            "issues": [],
        }


def test_plan_is_metadata_only_and_feather_coverage_never_counts_as_parquet(tmp_path):
    request = _request()
    adapter = FakeAdapter([_artifact(request, "2025-01"), _artifact(request, "2025-02")])
    catalog = FakeCatalog(
        status="AVAILABLE",
        partitions=[{
            "start": "2025-01-01T00:00:00Z", "end": "2025-02-01T00:00:00Z",
            "quality_status": "AVAILABLE", "source_path": "BTC.feather",
        }],
    )
    result = plan(request, adapter, catalog, dry_run=True)
    assert [period.status.value for period in result.months] == ["PLANNED", "PLANNED"]
    assert adapter.download_calls == adapter.read_calls == 0
    assert list(tmp_path.iterdir()) == []


def test_plan_uses_verified_parquet_coverage_and_distinguishes_missing_source():
    request = _request()
    january = _artifact(request, "2025-01", AvailabilityStatus.AVAILABLE)
    february = _artifact(request, "2025-02", AvailabilityStatus.UNAVAILABLE)
    adapter = FakeAdapter([january, february])
    catalog = FakeCatalog(
        status="AVAILABLE",
        partitions=[{
            "start": "2025-01-01T00:00:00Z", "end": "2025-02-01T00:00:00Z",
            "quality_status": "AVAILABLE", "storage_format": "parquet",
            "source_path": "BTC.parquet",
        }],
    )
    result = plan(request, adapter, catalog)
    assert [period.status.value for period in result.months] == ["PRESENT", "UNAVAILABLE"]


def test_plan_never_falls_back_from_invalid_latest_catalog_revision():
    request = _request()
    adapter = FakeAdapter([_artifact(request, "2025-01"), _artifact(request, "2025-02")])
    result = plan(request, adapter, FakeCatalog(status="INVALID"))
    assert {period.status.value for period in result.months} == {"INVALID"}


def test_plan_reports_missing_month_when_local_source_has_no_artifact():
    request = _request()
    result = plan(request, FakeAdapter([_artifact(request, "2025-01")]), FakeCatalog())
    assert [period.status.value for period in result.months] == ["PLANNED", "MISSING"]


def test_plan_rejects_ambiguous_local_sources_for_same_month():
    request = _request(start="2025-01-01", end="2025-02-01")
    first = _artifact(request, "2025-01")
    second = replace(first, source_locator="other-root:BTC.feather")
    result = plan(request, FakeAdapter([first, second]), FakeCatalog())
    assert result.months[0].status.value == "INVALID"
    assert result.months[0].issue_codes == ("AMBIGUOUS_SOURCE_ARTIFACTS",)


def _batch(artifact, frame, *, source_bytes=b"raw source"):
    return ObservationBatch(
        identity=artifact.identity,
        artifact=artifact,
        observations=frame,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )


def test_validate_builds_digest_bound_receipt_and_keeps_sparse_events_sparse():
    request = _request(kind="funding_rate", start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01")
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-17T04:00:00Z"], utc=True),
        "funding_rate": [0.001, -0.002],
    })
    receipt = validate(_batch(artifact, frame), converter_version="test-1")
    assert receipt.quality_status == "AVAILABLE"
    assert receipt.row_count == 2
    assert receipt.source_sha256 == hashlib.sha256(b"raw source").hexdigest()
    assert len(receipt.normalized_sha256) == 64
    assert receipt.start == artifact.start and receipt.end == artifact.end
    repeated = validate(_batch(artifact, frame), converter_version="test-1")
    assert repeated.normalized_sha256 == receipt.normalized_sha256
    assert repeated.content_fingerprint == receipt.content_fingerprint


@pytest.mark.parametrize(
    "dates,values,issue",
    [
        (["2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"], [0.01, 0.02], "DUPLICATE_TIMESTAMP"),
        (["2025-01-01T00:05:00Z", "2025-01-01T00:00:00Z"], [0.01, 0.02], "UNSORTED_TIMESTAMP"),
        (["2025-01-01T00:00:00Z"], [float("inf")], "NONFINITE_VALUE"),
    ],
)
def test_validate_rejects_duplicate_unsorted_and_nonfinite_observations(dates, values, issue):
    request = _request(kind="funding_rate", start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01")
    frame = pd.DataFrame({"date": pd.to_datetime(dates, utc=True), "funding_rate": values})
    with pytest.raises(SourceValidationError) as exc:
        validate(_batch(artifact, frame), converter_version="test-1")
    assert issue in exc.value.issue_codes


def test_validate_detects_incomplete_vision_month_before_publication():
    request = _request(start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01", source_format="vision-monthly-zip-csv")
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [1.0],
    })
    with pytest.raises(SourceValidationError) as exc:
        validate(_batch(artifact, frame), converter_version="test-1")
    assert "INCOMPLETE_MONTH" in exc.value.issue_codes


def test_validate_rejects_internal_candle_gaps_but_allows_explicit_partial_bounds():
    request = _request(start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01")
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z"], utc=True),
        "open": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "close": [10.5, 10.5], "volume": [1.0, 1.0],
    })
    with pytest.raises(SourceValidationError) as exc:
        validate(_batch(artifact, frame), converter_version="test-1", policy=ValidationPolicy(allow_partial=True))
    assert "MISSING_CANDLES" in exc.value.issue_codes

    contiguous = frame.iloc[:1].copy()
    contiguous["date"] = pd.to_datetime(["2025-01-01T00:05:00Z"], utc=True)
    bounded = replace(artifact, start=datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc),
                      end=datetime(2025, 1, 1, 0, 15, tzinfo=timezone.utc))
    with pytest.raises(SourceValidationError, match="INCOMPLETE_SOURCE_COVERAGE"):
        validate(_batch(bounded, contiguous), converter_version="test-1")
    receipt = validate(
        _batch(bounded, contiguous), converter_version="test-1",
        policy=ValidationPolicy(allow_partial=True),
    )
    assert receipt.quality_status == "PARTIAL"


def test_validate_rejects_observations_outside_declared_artifact_bounds():
    request = _request(kind="funding_rate", start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01")
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-02-01T00:00:00Z"], utc=True),
        "funding_rate": [0.001],
    })
    with pytest.raises(SourceValidationError) as exc:
        validate(_batch(artifact, frame), converter_version="test-1")
    assert "OBSERVATION_OUT_OF_BOUNDS" in exc.value.issue_codes


def test_validate_rejects_invalid_ohlcv_price_bounds():
    request = _request(start="2025-01-01", end="2025-02-01")
    artifact = _artifact(request, "2025-01")
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
        "open": [10.0], "high": [9.0], "low": [8.0], "close": [8.5], "volume": [1.0],
    })
    with pytest.raises(SourceValidationError) as exc:
        validate(_batch(artifact, frame), converter_version="test-1", policy=ValidationPolicy(allow_partial=True))
    assert "INVALID_OHLC_BOUNDS" in exc.value.issue_codes


def test_validate_rechecks_local_source_digest_after_read(tmp_path):
    request = _request(kind="funding_rate", start="2025-01-01", end="2025-02-01")
    source_path = tmp_path / "source.feather"
    source_path.write_bytes(b"original source")
    artifact = replace(_artifact(request, "2025-01"), local_path=source_path)
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
        "funding_rate": [0.001],
    })
    batch = _batch(artifact, frame, source_bytes=b"original source")
    source_path.write_bytes(b"changed after read")
    with pytest.raises(SourceValidationError) as exc:
        validate(batch, converter_version="test-1")
    assert exc.value.issue_codes == ("SOURCE_CHANGED",)


def test_journal_recovers_object_without_catalog_registration(tmp_path):
    journal = JournalStore(tmp_path / "journal")
    key = "dataset-id/2025-01"
    journal.transition(key, JournalState.DISCOVERED, request_id="request", dataset_id="dataset")
    journal.transition(key, JournalState.DOWNLOADED, request_id="request", dataset_id="dataset")
    journal.transition(key, JournalState.VALIDATED, request_id="request", dataset_id="dataset")
    journal.transition(key, JournalState.OBJECT_PUBLISHED, request_id="request", dataset_id="dataset")
    recovered = journal.transition(
        key, JournalState.CATALOG_REGISTERED, request_id="request", dataset_id="dataset"
    )
    assert recovered["status"] == "CATALOG_REGISTERED"
    assert journal.read(key)["status"] == "CATALOG_REGISTERED"
    with pytest.raises(ValueError, match="transition"):
        journal.transition(key, JournalState.DOWNLOADED, request_id="request", dataset_id="dataset")
