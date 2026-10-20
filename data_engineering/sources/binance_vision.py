"""Bounded, checksum-verified adapter for Binance Vision USD-M monthly klines."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..catalog_v3 import _reject_symlink_components
from .contracts import (
    AdapterCapability,
    AvailabilityStatus,
    DatasetRequest,
    DownloadResult,
    DownloadStatus,
    ObservationBatch,
    SourceArtifact,
)
from .vision_archive import (
    TIMEFRAME_SECONDS,
    archive_url,
    checksum_expected,
    discover_archive_months,
    parse_kline_zip,
)


CRYPTO_ASSETS = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP"})
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})


class ResponseTooLargeError(ValueError):
    """Raised before an oversized HTTP response is retained in memory."""


def _validate_https_url(url: str, *, expected_host: str | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("Vision request URL must be a credential-free HTTPS URL")
    allowed = {"data.binance.vision", "s3-ap-northeast-1.amazonaws.com"}
    if parsed.hostname not in allowed or (expected_host and parsed.hostname != expected_host):
        raise ValueError("Vision request URL host is not allowlisted")
    if parsed.hostname == "s3-ap-northeast-1.amazonaws.com" and not parsed.path.startswith("/data.binance.vision"):
        raise ValueError("Vision metadata path is outside the public archive bucket")
    if parsed.hostname == "data.binance.vision" and not parsed.path.startswith("/data/futures/um/monthly/klines/"):
        raise ValueError("Vision archive path is outside monthly USD-M klines")
    return parsed.hostname


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        initial = urlparse(request.full_url)
        host = _validate_https_url(new_url, expected_host=initial.hostname)
        if host != initial.hostname:
            raise ValueError("Vision redirect host is not allowlisted")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


class SafeVisionHTTPClient:
    """HTTPS client with same-host redirects, bounded reads, retries, and timeouts."""

    def __init__(self, *, retries: int = 2, timeout: int = 25, user_agent: str = "AlphaLab-Vision-Adapter/1.0"):
        if not 0 <= retries <= 3 or not 1 <= timeout <= 60:
            raise ValueError("retries or timeout exceeds the supported bound")
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.opener = build_opener(_AllowlistedRedirectHandler())

    def fetch(self, url: str, *, method: str, retries: int | None = None, timeout: int | None = None):
        host = _validate_https_url(url)
        attempts = self.retries if retries is None else min(max(0, retries), self.retries)
        request_timeout = self.timeout if timeout is None else min(max(1, timeout), self.timeout)
        if host == "s3-ap-northeast-1.amazonaws.com":
            limit = MAX_RESPONSE_BYTES
        elif url.endswith(".CHECKSUM"):
            limit = 64 * 1024
        else:
            limit = MAX_ARCHIVE_BYTES
        for attempt in range(attempts + 1):
            request = Request(url, headers={"User-Agent": self.user_agent}, method=method)
            try:
                with self.opener.open(request, timeout=request_timeout) as response:
                    final_url = response.geturl()
                    _validate_https_url(final_url, expected_host=host)
                    if method == "HEAD":
                        return int(response.status), None, final_url
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise ResponseTooLargeError("Vision response exceeds configured size limit")
                    return int(response.status), body, final_url
            except HTTPError as exc:
                if exc.code in {403, 404, 451}:
                    return int(exc.code), None, url
                if exc.code not in TRANSIENT_HTTP or attempt == attempts:
                    raise
            except (TimeoutError, OSError, URLError):
                if attempt == attempts:
                    raise
            time.sleep(min(2**attempt, 4))
        raise RuntimeError("Vision request retries exhausted")


def _month_interval(month: str) -> tuple[datetime, datetime]:
    year, month_number = map(int, month.split("-"))
    start = datetime(year, month_number, 1, tzinfo=timezone.utc)
    end = datetime(year + (month_number == 12), 1 if month_number == 12 else month_number + 1,
                   1, tzinfo=timezone.utc)
    return start, end


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BinanceVisionAdapter:
    """Discover, checksum-download, and parse only reviewed crypto Futures bars."""

    name = "binance-vision-usdm-crypto"
    capabilities = frozenset({AdapterCapability.DISCOVER, AdapterCapability.READ, AdapterCapability.DOWNLOAD})

    def __init__(
        self,
        http_client: Any,
        raw_root: Path,
        *,
        allow_1m: bool = False,
        max_archive_bytes: int = MAX_ARCHIVE_BYTES,
    ) -> None:
        if http_client is None or not callable(getattr(http_client, "fetch", None)):
            raise ValueError("http_client must provide fetch(url, method=...)")
        if not 1 <= max_archive_bytes <= MAX_ARCHIVE_BYTES:
            raise ValueError("max_archive_bytes must be within the hard 256 MiB bound")
        self.http_client = http_client
        self.raw_root = Path(raw_root)
        self.allow_1m = allow_1m
        self.max_archive_bytes = max_archive_bytes

    @staticmethod
    def crypto_symbol(asset: str) -> str:
        if asset not in CRYPTO_ASSETS:
            raise ValueError(f"asset is outside reviewed crypto allowlist: {asset}")
        return f"{asset}USDT"

    def _validate_request(self, request: DatasetRequest) -> tuple[str, list[str]]:
        identity = request.identity
        if identity.exchange != "binance" or identity.market_type != "futures" or identity.data_kind != "ohlcv":
            raise ValueError("Vision adapter supports Binance USD-M Futures OHLCV only")
        symbol = self.crypto_symbol(identity.asset)
        if identity.exchange_symbol != f"{identity.asset}/USDT:USDT":
            raise ValueError("exchange symbol does not match reviewed USDT-margined identity")
        if identity.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("unsupported Vision timeframe")
        if identity.timeframe == "1m" and not self.allow_1m:
            raise ValueError("1m Vision discovery requires explicit opt-in")
        if identity.timeframe == "1m" and symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("1m Vision experiment is limited to BTCUSDT or ETHUSDT")
        cursor = request.start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = request.end
        months = []
        while cursor < end:
            months.append(cursor.strftime("%Y-%m"))
            year, month = cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1
            cursor = cursor.replace(year=year, month=month)
        return symbol, months

    def _fetch(self, url: str, *, method: str, retries: int = 2, timeout: int = 25):
        parsed_host = _validate_https_url(url)
        result = self.http_client.fetch(url, method=method, retries=retries, timeout=timeout)
        if len(result) == 2:
            status, body = result
            final_url = url
        elif len(result) == 3:
            status, body, final_url = result
        else:
            raise ValueError("HTTP client returned an invalid response tuple")
        final_host = _validate_https_url(final_url, expected_host=parsed_host)
        if final_host != parsed_host:
            raise ValueError("Vision redirect host is not allowlisted")
        return status, body

    def discover(self, request: DatasetRequest) -> list[SourceArtifact]:
        symbol, months = self._validate_request(request)
        listed = set(discover_archive_months(symbol, request.identity.timeframe, fetcher=self._fetch))
        result = []
        for month in months:
            start, end = _month_interval(month)
            url, _ = archive_url(symbol, request.identity.timeframe, month)
            result.append(
                SourceArtifact(
                    identity=request.identity,
                    request_id=request.request_id,
                    source_locator=url,
                    source_format="vision-monthly-zip-csv",
                    start=start,
                    end=end,
                    availability=(AvailabilityStatus.AVAILABLE if month in listed
                                  else AvailabilityStatus.UNAVAILABLE),
                )
            )
        return result

    def _validate_artifact_url(self, artifact: SourceArtifact) -> tuple[str, str, str]:
        symbol = self.crypto_symbol(artifact.identity.asset)
        month = artifact.start.strftime("%Y-%m")
        expected_url, name = archive_url(symbol, artifact.identity.timeframe, month)
        if artifact.source_locator != expected_url or artifact.source_format != "vision-monthly-zip-csv":
            raise ValueError("artifact locator or format does not match reviewed Vision identity")
        if (artifact.start, artifact.end) != _month_interval(month):
            raise ValueError("Vision artifact must cover one complete UTC calendar month")
        _validate_https_url(expected_url, expected_host="data.binance.vision")
        return expected_url, name, month

    def download(self, artifact: SourceArtifact, destination: Path) -> DownloadResult:
        url, name, _ = self._validate_artifact_url(artifact)
        if artifact.availability is AvailabilityStatus.UNAVAILABLE:
            return DownloadResult(DownloadStatus.UNAVAILABLE)
        root = Path(destination).absolute()
        configured = self.raw_root.absolute()
        try:
            root.relative_to(configured)
        except ValueError as exc:
            raise ValueError("raw cache destination is outside configured raw root") from exc
        _reject_symlink_components(root, description="raw cache")
        root.mkdir(parents=True, exist_ok=True)
        try:
            status, checksum = self._fetch(url + ".CHECKSUM", method="GET")
            if status == 404:
                archive_status, _ = self._fetch(url, method="HEAD")
                if archive_status == 404:
                    return DownloadResult(DownloadStatus.UNAVAILABLE)
                if archive_status == 200:
                    return DownloadResult(DownloadStatus.CHECKSUM_FAILED, issue_code="MISSING_CHECKSUM")
                return DownloadResult(DownloadStatus.DOWNLOAD_FAILED, issue_code=f"ARCHIVE_HTTP_{archive_status}")
            if status != 200 or checksum is None:
                return DownloadResult(DownloadStatus.DOWNLOAD_FAILED, issue_code=f"CHECKSUM_HTTP_{status}")
            if len(checksum) > 64 * 1024:
                raise ResponseTooLargeError("Vision checksum response exceeds configured size limit")
            expected = checksum_expected(checksum, name)
            content_path = root / f"{expected}.zip"
            sidecar_path = root / f"{expected}.CHECKSUM"
            if content_path.is_symlink() or sidecar_path.is_symlink():
                return DownloadResult(DownloadStatus.DOWNLOAD_FAILED, issue_code="CACHE_SYMLINK")
            if content_path.exists():
                if _sha256_file(content_path) != expected:
                    return DownloadResult(DownloadStatus.CHECKSUM_FAILED, issue_code="CACHE_HASH_MISMATCH")
                if sidecar_path.exists() and sidecar_path.read_bytes() != checksum:
                    return DownloadResult(DownloadStatus.CHECKSUM_FAILED, issue_code="CACHE_CHECKSUM_MISMATCH")
                if not sidecar_path.exists():
                    self._atomic_create(sidecar_path, checksum)
                return DownloadResult(DownloadStatus.ALREADY_CACHED, content_path)
            status, body = self._fetch(url, method="GET")
            if status == 404:
                return DownloadResult(DownloadStatus.UNAVAILABLE)
            if status != 200 or body is None:
                return DownloadResult(DownloadStatus.DOWNLOAD_FAILED, issue_code=f"ARCHIVE_HTTP_{status}")
            if len(body) > self.max_archive_bytes:
                raise ResponseTooLargeError("Vision archive exceeds configured size limit")
            digest = hashlib.sha256(body).hexdigest()
            if digest != expected:
                return DownloadResult(DownloadStatus.CHECKSUM_FAILED, issue_code="CHECKSUM_MISMATCH")
            self._atomic_create(content_path, body)
            self._atomic_create(sidecar_path, checksum)
            return DownloadResult(DownloadStatus.DOWNLOADED, content_path)
        except ResponseTooLargeError:
            return DownloadResult(DownloadStatus.DOWNLOAD_FAILED, issue_code="RESPONSE_TOO_LARGE")
        except ValueError as exc:
            code = "CHECKSUM_MISMATCH" if "CHECKSUM" in str(exc) or "checksum" in str(exc).lower() else "RESPONSE_INVALID"
            status = DownloadStatus.CHECKSUM_FAILED if code == "CHECKSUM_MISMATCH" else DownloadStatus.DOWNLOAD_FAILED
            return DownloadResult(status, issue_code=code, detail=str(exc))
        except Exception as exc:
            return DownloadResult(
                DownloadStatus.DOWNLOAD_FAILED,
                issue_code=f"TRANSPORT_{type(exc).__name__.upper()}",
                detail=str(exc),
            )

    @staticmethod
    def _atomic_create(path: Path, data: bytes) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != data:
                    raise ValueError("immutable raw cache object conflicts with existing bytes")
        finally:
            temp.unlink(missing_ok=True)

    def read(self, artifact: SourceArtifact) -> ObservationBatch:
        _, _, month = self._validate_artifact_url(artifact)
        if artifact.local_path is None:
            raise ValueError("Vision artifact must reference a downloaded content-addressed ZIP")
        path = Path(artifact.local_path)
        root = _reject_symlink_components(self.raw_root, description="raw cache")
        resolved = _reject_symlink_components(path, description="raw archive")
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("raw archive is outside configured raw root") from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise ValueError("raw archive must be a regular file")
        source_digest = _sha256_file(resolved)
        if resolved.stem != source_digest:
            raise ValueError("raw archive path is not content-addressed")
        frame = parse_kline_zip(resolved, month=month, timeframe=artifact.identity.timeframe)
        return ObservationBatch(
            identity=artifact.identity,
            artifact=artifact,
            observations=frame,
            source_sha256=source_digest,
            metadata={
                "adapter": self.name,
                "source_url": artifact.source_locator,
                "source_archive_sha256": source_digest,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
