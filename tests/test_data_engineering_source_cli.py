from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_source_cli_requires_explicit_identity_roots_and_range(capsys):
    from data_engineering.sources.cli import main

    with pytest.raises(SystemExit):
        main(["plan"])
    error = capsys.readouterr().err
    assert "--adapter" in error and "--request-id" in error


def test_source_cli_dry_run_only_discovers_metadata(tmp_path, monkeypatch, capsys):
    from data_engineering.sources import cli
    from data_engineering.sources.contracts import (
        AvailabilityStatus,
        DatasetIdentity,
        SourceArtifact,
    )

    calls = []
    catalog_roots = []

    class Adapter:
        name = "fake"

        def discover(self, request):
            calls.append("discover")
            return [SourceArtifact(
                identity=request.identity, request_id=request.request_id,
                source_locator="fixture://artifact", source_format="fixture",
                start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end=datetime(2025, 2, 1, tzinfo=timezone.utc),
                availability=AvailabilityStatus.AVAILABLE,
            )]

        def download(self, *args):
            calls.append("download")
            raise AssertionError("dry-run must not download")

    monkeypatch.setattr(cli, "_build_adapter", lambda args, roots: Adapter())
    def catalog_view(*args, **kwargs):
        catalog_roots.append(kwargs["source_roots"])
        return type("Catalog", (), {"inspect": lambda self, identity: {"status": "MISSING"}})()

    monkeypatch.setattr(cli, "CatalogV3View", catalog_view)
    code = cli.main([
        "plan", "--adapter", "local", "--source-root", f"data={tmp_path}",
        "--catalog", str(tmp_path / "catalog"), "--exchange", "binance",
        "--normalized-root", str(tmp_path / "normalized"), "--dry-run",
        "--market-type", "futures", "--asset", "BTC",
        "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
        "--timeframe", "1h", "--start", "2025-01-01T00:00:00Z",
        "--end", "2025-02-01T00:00:00Z", "--request-id", "dry-run",
    ])
    response = json.loads(capsys.readouterr().out)
    assert code == 0
    assert response["months"][0]["status"] == "PLANNED"
    assert calls == ["discover"]
    assert catalog_roots == [{"data": tmp_path, "alpha-lab-normalized": tmp_path / "normalized"}]
    assert not (tmp_path / "catalog").exists()


def test_vision_discovery_needs_no_raw_cache_root(tmp_path, monkeypatch, capsys):
    from data_engineering.sources import cli
    from data_engineering.sources.contracts import AvailabilityStatus, SourceArtifact

    class Adapter:
        name = "fake-vision"

        def discover(self, request):
            return [SourceArtifact(
                identity=request.identity, request_id=request.request_id,
                source_locator="https://data.binance.vision/example.zip",
                source_format="vision-monthly-zip-csv",
                start=request.start, end=request.end,
                availability=AvailabilityStatus.AVAILABLE,
            )]

    monkeypatch.setattr(cli, "_build_adapter", lambda args, roots: Adapter())
    code = cli.main([
        "discover", "--adapter", "vision", "--catalog", str(tmp_path / "catalog"),
        "--exchange", "binance", "--market-type", "futures", "--asset", "BTC",
        "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
        "--timeframe", "1h", "--start", "2025-01-01T00:00:00Z",
        "--end", "2025-02-01T00:00:00Z", "--request-id", "vision-discovery",
    ])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result[0]["availability"] == "AVAILABLE"


def test_local_download_command_reports_unsupported(tmp_path, capsys):
    from data_engineering.sources.cli import main
    from data_engineering.sources.contracts import UnsupportedCapabilityError

    with pytest.raises(UnsupportedCapabilityError, match="does not support download"):
        main([
            "download", "--adapter", "local", "--source-root", f"data={tmp_path}",
            "--catalog", str(tmp_path / "catalog"), "--exchange", "binance",
            "--market-type", "futures", "--asset", "BTC",
            "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
            "--timeframe", "1h", "--start", "2025-01-01T00:00:00Z",
            "--end", "2025-02-01T00:00:00Z", "--request-id", "local-download",
        ])


def test_sources_cli_has_no_agent_download_interface():
    from data_engineering.sources import cli

    assert not hasattr(cli, "mcp")
    assert not hasattr(cli, "agent")


def test_validation_journal_details_preserve_downloaded_local_path(tmp_path):
    from data_engineering.sources.cli import _validation_details
    from data_engineering.sources.contracts import (
        AvailabilityStatus, DatasetIdentity, SourceArtifact, ValidationReceipt,
    )

    identity = DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "1h")
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 2, 1, tzinfo=timezone.utc)
    archive = tmp_path / "archive.zip"
    artifact = SourceArtifact(
        identity=identity, request_id="request", source_locator="https://example.invalid/a.zip",
        source_format="vision-monthly-zip-csv", start=start, end=end,
        availability=AvailabilityStatus.AVAILABLE, local_path=archive,
    )
    receipt = ValidationReceipt(
        dataset_id=identity.dataset_id(), request_id="request", source_sha256="a" * 64,
        normalized_sha256="b" * 64, converter_version="v1", schema_version="v1",
        start=start, end=end, validation_fingerprint="c" * 64,
    )

    assert _validation_details(receipt, artifact)["local_path"] == str(archive)


def test_sync_journal_mapping_preserves_checksum_failure():
    from data_engineering.sources.cli import _download_journal_state
    from data_engineering.sources.contracts import DownloadStatus
    from data_engineering.sources.journal import JournalState

    assert _download_journal_state(DownloadStatus.CHECKSUM_FAILED) is JournalState.CHECKSUM_FAILED


def test_download_stage_retries_failed_artifact_and_keeps_history(tmp_path, monkeypatch, capsys):
    from data_engineering.sources import cli
    from data_engineering.sources.contracts import (
        AvailabilityStatus, DatasetIdentity, DownloadResult, DownloadStatus, SourceArtifact,
    )
    from data_engineering.sources.journal import JournalState, JournalStore

    identity = DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "1h")
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 2, 1, tzinfo=timezone.utc)
    artifact = SourceArtifact(
        identity=identity, request_id="retry-request", source_locator="fixture://artifact",
        source_format="fixture", start=start, end=end,
        availability=AvailabilityStatus.AVAILABLE,
    )

    class Adapter:
        name = "retry-adapter"
        capabilities = frozenset({"discover", "download"})

        def discover(self, request):
            return [artifact]

        def download(self, source_artifact, destination):
            return DownloadResult(DownloadStatus.DOWNLOADED, local_path=tmp_path / "cached.zip")

    monkeypatch.setattr(cli, "_build_adapter", lambda args, roots: Adapter())
    catalog = tmp_path / "catalog"
    journal = JournalStore(catalog / "source-journal")
    key = cli._artifact_key(artifact)
    journal.transition(key, JournalState.DISCOVERED, request_id=artifact.request_id,
                       dataset_id=identity.dataset_id())
    journal.transition(key, JournalState.DOWNLOAD_FAILED, request_id=artifact.request_id,
                       dataset_id=identity.dataset_id(), details={"issue_code": "TEMPORARY"})

    cli.main([
        "download", "--adapter", "local", "--source-root", f"data={tmp_path}",
        "--catalog", str(catalog), "--exchange", "binance", "--market-type", "futures",
        "--asset", "BTC", "--exchange-symbol", "BTC/USDT:USDT", "--data-kind", "ohlcv",
        "--timeframe", "1h", "--start", start.isoformat(), "--end", end.isoformat(),
        "--request-id", artifact.request_id,
    ])
    result = journal.read(key)
    assert result["status"] == "DOWNLOADED"
    assert result["failure_history"][0]["status"] == "DOWNLOAD_FAILED"
    assert json.loads(capsys.readouterr().out)[0]["status"] == "DOWNLOADED"
