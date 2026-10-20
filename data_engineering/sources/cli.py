"""Explicit local operator CLI for staged source preparation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .binance_vision import BinanceVisionAdapter, SafeVisionHTTPClient
from .contracts import (
    AdapterCapability, DatasetIdentity, DatasetRequest, SourceArtifact,
    UnsupportedCapabilityError, ValidationReceipt, download_artifact,
)
from .freqtrade import FreqtradeFeatherAdapter
from .journal import JournalState, JournalStore
from .parquet import publish as publish_parquet
from .pipeline import (
    CatalogV3View, SourceValidationError, ValidationPolicy, plan, validate,
)


def _source_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("source roots must use SOURCE_ROOT_ID=PATH")
        root_id, path = value.split("=", 1)
        if not root_id or not path or root_id in roots:
            raise ValueError("source roots require unique IDs and nonempty paths")
        roots[root_id] = Path(path)
    return roots


def _utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date bounds must be ISO-8601 UTC timestamps") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError("date bounds must include a UTC timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare immutable normalized source partitions")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("discover", "Discover bounded source artifacts"),
        ("plan", "Compare source artifacts with verified normalized catalog coverage"),
        ("download", "Download source artifacts when supported by the selected adapter"),
        ("validate", "Validate source data and persist digest-bound receipts"),
        ("publish", "Publish validated immutable Parquet partitions"),
        ("sync", "Resume download, validation, and publication stages"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--adapter", choices=("local", "vision"), required=True)
        command.add_argument("--source-root", action="append", default=[], metavar="ID=PATH")
        command.add_argument("--catalog", type=Path, required=True)
        command.add_argument("--exchange", required=True)
        command.add_argument("--market-type", choices=("spot", "futures"), required=True)
        command.add_argument("--asset", required=True)
        command.add_argument("--exchange-symbol", required=True)
        command.add_argument("--data-kind", choices=("ohlcv", "funding_rate", "open_interest", "mark_price"), required=True)
        command.add_argument("--timeframe", required=True)
        command.add_argument("--start", type=_utc_datetime, required=True)
        command.add_argument("--end", type=_utc_datetime, required=True)
        command.add_argument("--request-id", required=True)
        command.add_argument("--event-cadence")
        command.add_argument("--raw-root", type=Path)
        command.add_argument("--normalized-root", type=Path)
        command.add_argument("--normalized-root-id", default="alpha-lab-normalized")
        command.add_argument("--allow-1m", action="store_true")
        command.add_argument("--allow-partial", action="store_true")
        command.add_argument("--converter-version", default="data-engineering-parquet-v1")
        command.add_argument("--month", help="Limit the operation to one YYYY-MM artifact")
        if name == "plan":
            command.add_argument("--dry-run", action="store_true", required=True)
    return parser


def _request(args: argparse.Namespace) -> DatasetRequest:
    identity = DatasetIdentity(
        exchange=args.exchange, market_type=args.market_type, asset=args.asset,
        exchange_symbol=args.exchange_symbol, data_kind=args.data_kind,
        timeframe=args.timeframe,
    )
    return DatasetRequest(identity, args.start, args.end, args.request_id)


def _build_adapter(args: argparse.Namespace, roots: dict[str, Path]):
    if args.adapter == "local":
        if not roots:
            raise ValueError("local adapter requires at least one --source-root ID=PATH")
        return FreqtradeFeatherAdapter(
            roots, exchange=args.exchange, market_type=args.market_type,
            event_cadence=args.event_cadence,
        )
    if args.raw_root is None:
        raise ValueError("Vision adapter requires --raw-root")
    return BinanceVisionAdapter(
        SafeVisionHTTPClient(), args.raw_root, allow_1m=args.allow_1m,
    )


def _artifact_key(artifact: SourceArtifact) -> str:
    return "|".join((
        artifact.identity.dataset_id(), artifact.request_id, artifact.source_locator,
        artifact.start.isoformat(), artifact.end.isoformat(),
    ))


def _select_artifacts(adapter, request: DatasetRequest, month: str | None) -> list[SourceArtifact]:
    artifacts = adapter.discover(request)
    if month:
        if len(month) != 7 or month[4] != "-" or not month[:4].isdigit() or not month[5:].isdigit():
            raise ValueError("--month must use YYYY-MM")
        artifacts = [item for item in artifacts if item.start.strftime("%Y-%m") == month]
        if not artifacts:
            raise ValueError(f"no source artifact discovered for {month}")
    return artifacts


def _receipt_dict(receipt: ValidationReceipt) -> dict[str, Any]:
    record = asdict(receipt)
    record["start"] = receipt.start.isoformat()
    record["end"] = receipt.end.isoformat()
    record["issue_codes"] = list(receipt.issue_codes)
    return record


def _load_receipt(record: dict[str, Any]) -> ValidationReceipt:
    record = dict(record)
    record["start"] = datetime.fromisoformat(record["start"])
    record["end"] = datetime.fromisoformat(record["end"])
    record["issue_codes"] = tuple(record.get("issue_codes", ()))
    return ValidationReceipt(**record)


def _validation_details(receipt: ValidationReceipt, artifact: SourceArtifact) -> dict[str, Any]:
    """Retain downloaded source location alongside the validation receipt."""
    return {
        "receipt": _receipt_dict(receipt),
        "local_path": str(artifact.local_path) if artifact.local_path else None,
    }


def _artifact_for_read(adapter, artifact: SourceArtifact, journal: JournalStore) -> SourceArtifact:
    record = journal.read(_artifact_key(artifact))
    if artifact.local_path is None and record:
        local_path = record.get("details", {}).get("local_path")
        if local_path:
            return replace(artifact, local_path=Path(local_path))
    return artifact


def _process_stage(args: argparse.Namespace, artifacts, adapter, roots, journal) -> list[dict[str, Any]]:
    results = []
    request_id = args.request_id
    for artifact in artifacts:
        key = _artifact_key(artifact)
        dataset_id = artifact.identity.dataset_id()
        current = journal.read(key)
        if current and current["status"] == JournalState.CATALOG_REGISTERED.value:
            results.append({"month": artifact.start.strftime("%Y-%m"),
                            "status": JournalState.CATALOG_REGISTERED.value,
                            **current.get("details", {})})
            continue
        if args.command == "download" and current and current["status"] in {
            JournalState.DOWNLOADED.value, JournalState.VALIDATED.value,
            JournalState.OBJECT_PUBLISHED.value,
        }:
            results.append({"month": artifact.start.strftime("%Y-%m"), "status": current["status"]})
            continue
        if current is None:
            current = journal.transition(
                key, JournalState.DISCOVERED, request_id=request_id, dataset_id=dataset_id,
                details={"source_locator": artifact.source_locator},
            )
        if args.command == "download":
            result = download_artifact(adapter, artifact, (args.raw_root or Path(".")))
            state = {
                "DOWNLOADED": JournalState.DOWNLOADED,
                "ALREADY_CACHED": JournalState.DOWNLOADED,
                "UNAVAILABLE": JournalState.UNAVAILABLE,
                "DOWNLOAD_FAILED": JournalState.DOWNLOAD_FAILED,
                "CHECKSUM_FAILED": JournalState.CHECKSUM_FAILED,
            }[result.status.value]
            journal.transition(key, state, request_id=request_id, dataset_id=dataset_id,
                               details={"local_path": str(result.local_path) if result.local_path else None,
                                        "issue_code": result.issue_code, "detail": result.detail})
            results.append({"month": artifact.start.strftime("%Y-%m"), "status": state.value,
                            "issue_code": result.issue_code})
            continue

        artifact = _artifact_for_read(adapter, artifact, journal)
        if args.command == "validate":
            try:
                batch = adapter.read(artifact)
            except Exception as exc:
                journal.transition(key, JournalState.PARSE_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"detail": str(exc)})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.PARSE_FAILED.value, "detail": str(exc)})
                continue
            try:
                receipt = validate(batch, converter_version=args.converter_version,
                                   policy=ValidationPolicy(allow_partial=args.allow_partial))
            except SourceValidationError as exc:
                journal.transition(key, JournalState.QUALITY_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"issue_codes": list(exc.issue_codes),
                                                                  "report": exc.report})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.QUALITY_FAILED.value,
                                "issue_codes": list(exc.issue_codes)})
                continue
            journal.transition(key, JournalState.VALIDATED, request_id=request_id,
                               dataset_id=dataset_id,
                               details=_validation_details(receipt, artifact))
            results.append({"month": artifact.start.strftime("%Y-%m"), "status": "VALIDATED",
                            "receipt": _receipt_dict(receipt)})
            continue

        if args.command == "publish":
            if args.normalized_root is None:
                raise ValueError("publish requires --normalized-root")
            if not current or not current.get("details", {}).get("receipt"):
                raise ValueError("publish requires a persisted validation receipt; run validate first")
            batch = adapter.read(artifact)
            receipt = _load_receipt(current["details"]["receipt"])
            try:
                published = publish_parquet(
                    receipt, batch, {**roots, args.normalized_root_id: args.normalized_root},
                    args.catalog, normalized_root_id=args.normalized_root_id,
                )
            except Exception as exc:
                journal.transition(key, JournalState.PUBLISH_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"detail": str(exc)})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.PUBLISH_FAILED.value, "detail": str(exc)})
                continue
            journal.transition(key, JournalState.CATALOG_REGISTERED, request_id=request_id,
                               dataset_id=dataset_id, details={"object_path": str(published.object_path),
                                                               "publish_status": published.status.value})
            results.append({"month": artifact.start.strftime("%Y-%m"),
                            "status": "CATALOG_REGISTERED", "publish_status": published.status.value,
                            "object_path": str(published.object_path)})
            continue

        if args.command == "sync":
            batch_artifact = artifact
            if adapter.capabilities and "download" in {str(item) for item in adapter.capabilities}:
                downloaded = download_artifact(adapter, artifact, args.raw_root)
                if downloaded.local_path is None:
                    state = JournalState.UNAVAILABLE if downloaded.status.value == "UNAVAILABLE" else JournalState.DOWNLOAD_FAILED
                    journal.transition(key, state, request_id=request_id, dataset_id=dataset_id,
                                       details={"issue_code": downloaded.issue_code})
                    results.append({"month": artifact.start.strftime("%Y-%m"), "status": state.value})
                    continue
                batch_artifact = replace(artifact, local_path=downloaded.local_path)
            try:
                batch = adapter.read(batch_artifact)
            except Exception as exc:
                journal.transition(key, JournalState.PARSE_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"detail": str(exc)})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.PARSE_FAILED.value, "detail": str(exc)})
                continue
            try:
                receipt = validate(batch, converter_version=args.converter_version,
                                   policy=ValidationPolicy(allow_partial=args.allow_partial))
            except SourceValidationError as exc:
                journal.transition(key, JournalState.QUALITY_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"issue_codes": list(exc.issue_codes),
                                                                  "report": exc.report})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.QUALITY_FAILED.value,
                                "issue_codes": list(exc.issue_codes)})
                continue
            journal.transition(key, JournalState.VALIDATED, request_id=request_id,
                               dataset_id=dataset_id,
                               details=_validation_details(receipt, batch_artifact))
            if args.normalized_root is None:
                raise ValueError("sync requires --normalized-root")
            try:
                published = publish_parquet(
                    receipt, batch, {**roots, args.normalized_root_id: args.normalized_root},
                    args.catalog, normalized_root_id=args.normalized_root_id,
                )
            except Exception as exc:
                journal.transition(key, JournalState.PUBLISH_FAILED, request_id=request_id,
                                   dataset_id=dataset_id, details={"detail": str(exc)})
                results.append({"month": artifact.start.strftime("%Y-%m"),
                                "status": JournalState.PUBLISH_FAILED.value, "detail": str(exc)})
                continue
            journal.transition(key, JournalState.CATALOG_REGISTERED, request_id=request_id,
                               dataset_id=dataset_id, details={"object_path": str(published.object_path),
                                                               "publish_status": published.status.value})
            results.append({"month": artifact.start.strftime("%Y-%m"),
                            "status": "CATALOG_REGISTERED", "publish_status": published.status.value,
                            "object_path": str(published.object_path)})
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    roots = _source_roots(args.source_root)
    request = _request(args)
    if args.command == "plan" and args.normalized_root is None:
        raise ValueError("plan requires --normalized-root to verify existing catalog coverage")
    if args.command in {"publish", "sync"} and args.normalized_root is None:
        raise ValueError(f"{args.command} requires --normalized-root")
    if args.adapter == "vision" and args.command in {"download", "sync"} and args.raw_root is None:
        raise ValueError(f"Vision {args.command} requires --raw-root")
    adapter = _build_adapter(args, roots)
    if args.command == "download" and AdapterCapability.DOWNLOAD not in adapter.capabilities:
        raise UnsupportedCapabilityError(f"adapter {adapter.name} does not support download")
    if args.command == "discover":
        artifacts = _select_artifacts(adapter, request, args.month)
        response = [{"month": item.start.strftime("%Y-%m"), "source_locator": item.source_locator,
                     "source_format": item.source_format, "availability": item.availability.value,
                     "start": item.start.isoformat(), "end": item.end.isoformat()}
                    for item in artifacts]
    elif args.command == "plan":
        catalog_roots = {**roots, args.normalized_root_id: args.normalized_root}
        result = plan(request, adapter, CatalogV3View(args.catalog, source_roots=catalog_roots), dry_run=True)
        response = {"dataset_id": result.dataset_id, "request_id": result.request_id,
                    "dry_run": True,
                    "months": [{"month": item.month, "status": item.status.value,
                                "issue_codes": list(item.issue_codes)} for item in result.months]}
    else:
        artifacts = _select_artifacts(adapter, request, args.month)
        journal_root = args.catalog / "source-journal"
        journal = JournalStore(journal_root)
        response = _process_stage(args, artifacts, adapter, roots, journal)
    print(json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
