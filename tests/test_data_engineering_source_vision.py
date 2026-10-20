"""Offline tests for the reviewed Binance Vision source adapter."""

from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from data_engineering.sources.contracts import (
    AvailabilityStatus, DatasetIdentity, DatasetRequest, DownloadStatus,
)
from data_engineering.sources.binance_vision import (
    BinanceVisionAdapter,
    MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    SafeVisionHTTPClient,
)


def _request(asset="BTC", timeframe="5m", start="2025-01-01", end="2025-03-01"):
    identity = DatasetIdentity(
        exchange="binance", market_type="futures", asset=asset,
        exchange_symbol=f"{asset}/USDT:USDT", data_kind="ohlcv", timeframe=timeframe,
    )
    return DatasetRequest(
        identity,
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
        "vision-request",
    )


def _listing(keys):
    return (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        '<IsTruncated>false</IsTruncated>'
        + "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
        + "</ListBucketResult>"
    ).encode()


def _zip_bytes(month="2025-01", timestamp="1735689600000"):
    stream = io.BytesIO()
    row = f"{timestamp},100,102,99,101,5,{int(timestamp)+299999},505,2,2,202,0\n"
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"BTCUSDT-5m-{month}.csv", row)
    return stream.getvalue()


class FakeHTTP:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch(self, url, *, method, retries=2, timeout=25):
        self.calls.append((url, method))
        if "s3-ap-northeast-1.amazonaws.com" in url:
            return 200, _listing([])
        result = self.responses.get((url, method), self.responses.get(url))
        if isinstance(result, Exception):
            raise result
        if result is None:
            return 404, None
        return result


def test_crypto_allowlist_is_separate_from_tradfi_and_1m_is_opt_in(tmp_path):
    allowed = {"BTC", "ETH", "SOL", "BNB", "XRP"}
    assert {BinanceVisionAdapter.crypto_symbol(asset) for asset in allowed} == {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"
    }
    with pytest.raises(ValueError, match="allowlist"):
        _adapter = BinanceVisionAdapter(FakeHTTP({}), tmp_path)
        _adapter.discover(_request(asset="NVDA"))
    with pytest.raises(ValueError, match="opt-in"):
        BinanceVisionAdapter(FakeHTTP({}), tmp_path).discover(_request(timeframe="1m"))
    BinanceVisionAdapter(FakeHTTP({}), tmp_path, allow_1m=True).discover(
        _request(timeframe="1m")
    )
    with pytest.raises(ValueError, match="BTCUSDT or ETHUSDT"):
        BinanceVisionAdapter(FakeHTTP({}), tmp_path, allow_1m=True).discover(
            _request(asset="SOL", timeframe="1m")
        )


def test_download_and_read_revalidate_full_artifact_identity_and_1m_opt_in(tmp_path):
    ordinary = BinanceVisionAdapter(FakeHTTP({}), tmp_path)
    artifact = ordinary.discover(_request())[0]
    wrong_exchange = replace(
        artifact, identity=replace(artifact.identity, exchange="other-exchange")
    )
    wrong_kind = replace(
        artifact, identity=replace(artifact.identity, data_kind="funding_rate")
    )
    for forged in (wrong_exchange, wrong_kind):
        with pytest.raises(ValueError, match="supports Binance"):
            ordinary._validate_artifact_url(forged)

    opted_in = BinanceVisionAdapter(FakeHTTP({}), tmp_path, allow_1m=True)
    one_minute = opted_in.discover(_request(timeframe="1m"))[0]
    without_opt_in = BinanceVisionAdapter(FakeHTTP({}), tmp_path, allow_1m=False)
    with pytest.raises(ValueError, match="opt-in"):
        without_opt_in._validate_artifact_url(one_minute)


def test_discovery_uses_bounded_s3_metadata_and_never_fetches_archive_payload(tmp_path):
    prefix = "data/futures/um/monthly/klines/BTCUSDT/5m/"
    listing = _listing([prefix + "BTCUSDT-5m-2025-01.zip"])
    class ListingHTTP(FakeHTTP):
        def fetch(self, url, *, method, retries=2, timeout=25):
            self.calls.append((url, method))
            if "s3-ap-northeast-1.amazonaws.com" in url:
                return 200, listing
            raise AssertionError("discovery attempted an archive request")

    http = ListingHTTP({})
    artifacts = BinanceVisionAdapter(http, tmp_path).discover(_request())
    assert [item.availability.value for item in artifacts] == ["AVAILABLE", "UNAVAILABLE"]
    assert [item.start.month for item in artifacts] == [1, 2]
    assert len(http.calls) == 1
    assert "s3-ap-northeast-1.amazonaws.com" in http.calls[0][0]
    assert all("data.binance.vision" in item.source_locator for item in artifacts)


def test_s3_listing_response_size_is_bounded(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=x"

        def read(self, limit):
            return b"x" * limit

    client = SafeVisionHTTPClient(retries=0)
    monkeypatch.setattr(client.opener, "open", lambda *args, **kwargs: Response())
    url = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=x"
    assert MAX_RESPONSE_BYTES == 4 * 1024 * 1024
    with pytest.raises(ResponseTooLargeError):
        client.fetch(url, method="GET")


def test_download_distinguishes_absent_archive_missing_checksum_and_transport(tmp_path):
    adapter = BinanceVisionAdapter(FakeHTTP({}), tmp_path)
    artifact = replace(adapter.discover(_request())[0], availability=AvailabilityStatus.AVAILABLE)
    url = artifact.source_locator
    no_archive = FakeHTTP({url + ".CHECKSUM": (404, None), (url, "HEAD"): (404, None)})
    result = BinanceVisionAdapter(no_archive, tmp_path).download(artifact, tmp_path / "raw")
    assert result.status is DownloadStatus.UNAVAILABLE
    assert [call[1] for call in no_archive.calls] == ["GET", "HEAD"]

    missing_sum = FakeHTTP({url + ".CHECKSUM": (404, None), (url, "HEAD"): (200, None)})
    result = BinanceVisionAdapter(missing_sum, tmp_path).download(artifact, tmp_path / "raw")
    assert result.status is DownloadStatus.CHECKSUM_FAILED
    assert result.issue_code == "MISSING_CHECKSUM"

    failed = FakeHTTP({url + ".CHECKSUM": RuntimeError("offline")})
    result = BinanceVisionAdapter(failed, tmp_path).download(artifact, tmp_path / "raw")
    assert result.status is DownloadStatus.DOWNLOAD_FAILED


def test_download_verifies_checksum_caches_raw_zip_and_reads_month(tmp_path):
    http = FakeHTTP({})
    adapter = BinanceVisionAdapter(http, tmp_path)
    artifact = replace(adapter.discover(_request())[0], availability=AvailabilityStatus.AVAILABLE)
    url = artifact.source_locator
    payload = _zip_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    checksum = f"{digest}  BTCUSDT-5m-2025-01.zip\n".encode()
    http.responses.update({
        url + ".CHECKSUM": (200, checksum),
        url: (200, payload),
    })
    downloaded = adapter.download(artifact, tmp_path / "raw")
    assert downloaded.status is DownloadStatus.DOWNLOADED
    assert hashlib.sha256(downloaded.local_path.read_bytes()).hexdigest() == digest
    batch = adapter.read(replace(artifact, local_path=downloaded.local_path))
    assert len(batch.observations) == 1
    assert batch.source_sha256 == digest
    cached = adapter.download(artifact, tmp_path / "raw")
    assert cached.status is DownloadStatus.ALREADY_CACHED
    assert cached.local_path == downloaded.local_path


def test_download_rejects_cross_host_redirect_without_writing(tmp_path):
    artifact = replace(
        BinanceVisionAdapter(FakeHTTP({}), tmp_path).discover(_request())[0],
        availability=AvailabilityStatus.AVAILABLE,
    )
    url = artifact.source_locator

    class RedirectArchive(FakeHTTP):
        def fetch(self, requested_url, *, method, retries=2, timeout=25):
            if requested_url.endswith(".CHECKSUM"):
                return 200, ("a" * 64 + "  BTCUSDT-5m-2025-01.zip\n").encode(), "https://evil.example/x"
            raise AssertionError("cross-host redirect must stop before archive request")

    destination = tmp_path / "raw-redirect"
    result = BinanceVisionAdapter(RedirectArchive({}), tmp_path).download(artifact, destination)
    assert result.status is DownloadStatus.DOWNLOAD_FAILED
    assert result.issue_code == "RESPONSE_INVALID"
    assert list(destination.iterdir()) == []


def test_download_rejects_checksum_mismatch_and_oversized_response(tmp_path):
    artifact = replace(
        BinanceVisionAdapter(FakeHTTP({}), tmp_path).discover(_request())[0],
        availability=AvailabilityStatus.AVAILABLE,
    )
    url = artifact.source_locator
    payload = _zip_bytes()
    checksum = ("0" * 64 + "  BTCUSDT-5m-2025-01.zip\n").encode()
    http = FakeHTTP({url + ".CHECKSUM": (200, checksum), url: (200, payload)})
    result = BinanceVisionAdapter(http, tmp_path).download(artifact, tmp_path / "raw")
    assert result.status is DownloadStatus.CHECKSUM_FAILED
    assert result.issue_code == "CHECKSUM_MISMATCH"

    class Oversized(FakeHTTP):
        def fetch(self, url, *, method, retries=2, timeout=25):
            if url.endswith(".CHECKSUM"):
                return 200, ("a" * 64 + "  BTCUSDT-5m-2025-01.zip\n").encode()
            return 200, b"x" * 101

    result = BinanceVisionAdapter(
        Oversized({}), tmp_path, max_archive_bytes=100
    ).download(artifact, tmp_path / "raw2")
    assert result.status is DownloadStatus.DOWNLOAD_FAILED
    assert result.issue_code == "RESPONSE_TOO_LARGE"


def test_discovery_rejects_listing_escape_and_redirect_host(tmp_path):
    class EscapingListing(FakeHTTP):
        def fetch(self, url, *, method, retries=2, timeout=25):
            return 200, _listing(["data/futures/um/monthly/klines/EVIL/"])

    with pytest.raises(ValueError, match="outside requested prefix"):
        BinanceVisionAdapter(EscapingListing({}), tmp_path).discover(_request())

    class RedirectHTTP(FakeHTTP):
        def fetch(self, url, *, method, retries=2, timeout=25):
            return 200, b"", "https://evil.example/redirected"

    with pytest.raises(ValueError, match="allowlisted"):
        BinanceVisionAdapter(RedirectHTTP({}), tmp_path).discover(_request())
