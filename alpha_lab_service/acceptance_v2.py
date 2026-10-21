"""Run the pinned v2 service against a verified Binance Vision BTC 5m month."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

import pandas as pd

from data_engineering.freqtrade_feather import register_feather_file
from data_engineering.sources.contracts import DatasetIdentity
from alpha_lab.data.availability import AvailabilityRuleRegistry, AvailabilityRuleV1
from alpha_lab.data.dataset_builder import DatasetBuilderV2
from alpha_lab.data.source_resolver import DatasetSourceResolver
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.authorization_v2 import MappingResearchRequestRegistryV1
from alpha_lab_service.broker_server import WorkerBroker
from alpha_lab_service.jobs_v2 import ResearchServiceV2
from alpha_lab_service.protocol_v2 import FactorRequestV2
from alpha_lab_service.store_v2 import ResearchStoreV2
from alpha_lab_service.worker_profile import profile_from_file


ARCHIVE_URL = (
    "https://data.binance.vision/data/futures/um/monthly/klines/"
    "BTCUSDT/5m/BTCUSDT-5m-2025-05.zip"
)
POLICY_SHA256 = "041efb79ddf9b88a71ff071cbfb52f833420658ff888cf6b6d87bb5f0a7ef209"
SOURCE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
STATEFUL_SOURCE = """\
def initialize_state(warmup, params):
    return {"steps": 0}

def compute_factor(history, *, as_of, params, state):
    updated = {"steps": state["steps"] + 1}
    return {"BTCUSDT": float(history["close"].iloc[-1])}, updated
"""
STATELESS_SOURCE = """\
def compute_factor(history, *, as_of, params):
    return {"BTCUSDT": float(history["close"].iloc[-1])}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_source(archive: Path, checksum_file: Path, root: Path) -> tuple[dict, str]:
    expected = checksum_file.read_text(encoding="ascii").split()[0].lower()
    actual = _sha256(archive)
    if actual != expected:
        raise ValueError("archive SHA-256 does not match the published checksum sidecar")
    source_root = root / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    feather = source_root / "BTC_USDT_USDT-5m-futures.feather"
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError("archive must contain exactly one candle CSV")
        with zf.open(members[0]) as csv_stream:
            first_line = csv_stream.readline().decode("utf-8").strip().lower()
            csv_stream.seek(0)
            header = 0 if first_line.startswith("open_time,") else None
            frame = pd.read_csv(csv_stream, header=header, names=SOURCE_COLUMNS)
    frame["date"] = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
    frame.to_feather(feather)

    catalog_root = root / "catalog"
    registered = register_feather_file(
        catalog_root, source_root_id="vision-btc-monthly", source_root=source_root,
        relative_path=feather.name, exchange="binance", market_type="futures",
        asset="BTC", exchange_symbol="BTC/USDT:USDT", data_kind="ohlcv", timeframe="5m",
        source="binance-data-vision-monthly", source_reference=ARCHIVE_URL,
    )
    return {"root": source_root, "catalog": catalog_root, "registered": registered}, actual


def _build_trusted_components(root: Path, source):
    policy_registry = FileCampaignPolicyRegistry()
    identity = DatasetIdentity(
        exchange="binance", market_type="futures", asset="BTC",
        exchange_symbol="BTC/USDT:USDT", data_kind="ohlcv", timeframe="5m",
    )
    availability = AvailabilityRuleRegistry({
        "binance-close-plus-30s": AvailabilityRuleV1(
            "binance-close-plus-30s", identity, "fixed_delay", "date",
            fixed_delay=pd.Timedelta(seconds=30).to_pytimedelta(),
        ),
    })
    resolver = DatasetSourceResolver(
        source["catalog"], {"vision-btc-monthly": source["root"]}, exchange="binance",
        symbol_identities={"BTCUSDT": {
            "exchange": "binance", "market_type": "futures", "asset": "BTC",
            "exchange_symbol": "BTC/USDT:USDT",
        }}, availability_registry=availability,
    )
    builder = DatasetBuilderV2(policy_registry, resolver, availability, root / "dataset-output")
    request = {
        "protocol_version": "2.0-research-request", "request_id": "btc-5m-2025-05-factor-v2",
        "campaign_id": "example_campaign", "policy_version": "1",
        "campaign_policy_sha256": POLICY_SHA256,
        # A 30-second publication delay moves the final May decision to
        # 2025-06-01T00:00:30Z; one extra 5m boundary retains all May decisions.
        "start": "2025-05-01T00:00:00Z", "end": "2025-06-01T00:05:00Z",
        "symbols": ["BTCUSDT"], "timeframe": "5m", "mode": "time_series",
        "data_types": ["futures_ohlcv"], "split": "holdout", "lookback_bars": 20,
        "label_horizon_bars": 3, "embargo_bars": 1,
    }
    request_registry = MappingResearchRequestRegistryV1({request["request_id"]: request})
    options = DatasetBuildOptionsV1(
        "preserve", "futures_ohlcv", {}, {"futures_ohlcv": "binance-close-plus-30s"}, {},
    )
    return policy_registry, builder, request_registry, options, request["request_id"]


class _LocalWorkerBroker:
    def __init__(self, broker, *, cancel_before_chunk: int | None = None):
        self.broker = broker
        self.cancel_before_chunk = cancel_before_chunk
        self.calls = 0

    async def run_chunk(self, task_id, chunk_id, frames):
        self.calls += 1
        if self.calls == self.cancel_before_chunk:
            raise asyncio.CancelledError()
        return await asyncio.to_thread(self.broker.run_frames_v2, task_id, chunk_id, frames)


def _run_mode(root, run_key, *, mode, components, worker, cancel_before_chunk=None):
    policy_registry, builder, request_registry, options, request_id = components
    broker = _LocalWorkerBroker(worker, cancel_before_chunk=cancel_before_chunk)
    store = ResearchStoreV2(root / run_key / "experiments.sqlite3", protected_paths=())
    service = ResearchServiceV2(
        store, broker, research_request_registry=request_registry,
        policy_registry=policy_registry, dataset_builder=builder, dataset_options=options,
        worker_identity=worker.profile.image, evaluator_identity="alpha-lab-descriptive-v2",
    )
    factor = FactorRequestV2(
        research_request_id=request_id,
        source=STATEFUL_SOURCE if mode == "stateful" else STATELESS_SOURCE,
        worker_input_schema_id="ohlcv-v1", worker_input_schema_version="1",
        execution_mode=mode,
        state_schema_id="json-object-v1" if mode == "stateful" else None,
        state_schema_version="1" if mode == "stateful" else None,
    )
    submitted = service.submit_factor_v2(factor.model_dump(mode="json"))
    started = time.monotonic()
    try:
        result = asyncio.run(service.run_factor_v2(submitted["task_id"]))
    except asyncio.CancelledError:
        return {"service": service, "task_id": submitted["task_id"],
                "request_hash": submitted["request_hash"], "elapsed_seconds": time.monotonic() - started,
                "result": None}
    return {"service": service, "task_id": submitted["task_id"],
            "request_hash": submitted["request_hash"], "elapsed_seconds": time.monotonic() - started,
            "result": result}


def run_acceptance(archive: Path, checksum_file: Path, output_dir: Path, *, image: str | None = None) -> dict:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    profile_path = Path(__file__).resolve().parents[1] / "containers/alpha-lab-factor-worker/profile.json"
    pinned = profile_from_file(profile_path)
    if image is not None and image != pinned.image:
        raise ValueError("acceptance Worker image must match the committed immutable profile")
    image = pinned.image
    prepared, archive_sha256 = _prepare_source(Path(archive), Path(checksum_file), output_dir)
    components = _build_trusted_components(output_dir, prepared)
    policy_registry, builder, request_registry, options, request_id = components
    # Resolve once to report the schedule, then the service re-resolves before use.
    from alpha_lab_service.authorization_v2 import resolve_authorized_run
    from alpha_lab_service.chunking_v2 import plan_chunks

    request_payload = FactorRequestV2(
        research_request_id=request_id, source=STATELESS_SOURCE,
        worker_input_schema_id="ohlcv-v1", worker_input_schema_version="1",
        execution_mode="stateless",
    )
    authorized = resolve_authorized_run(
        request_payload, research_request_registry=request_registry,
        policy_registry=policy_registry, dataset_builder=builder, dataset_options=options,
        worker_identity=image, evaluator_identity="alpha-lab-descriptive-v2",
    )
    stateless_plan = plan_chunks(authorized)
    if stateless_plan.total_steps != 8_928:
        raise ValueError(f"expected 8,928 authorized decisions, got {stateless_plan.total_steps}")
    worker = WorkerBroker(replace(pinned, protocol_versions=("1.0", "2.0")))

    stateless = _run_mode(output_dir, "stateless", mode="stateless", components=components, worker=worker)
    if stateless["result"] is None or stateless["result"]["status"] not in {"DIAGNOSTIC", "EXPLORATORY"}:
        blocked = _blocked_result("stateless", stateless, archive_sha256, stateless_plan)
        (output_dir / "acceptance-v2.json").write_text(
            json.dumps(blocked, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        return blocked

    interrupted = _run_mode(
        output_dir, "stateful-restart", mode="stateful", components=components,
        worker=worker, cancel_before_chunk=2,
    )
    interruption_store = interrupted["service"].store
    no_partial_evidence = interruption_store.get_evidence(interrupted["request_hash"]) is None
    resumed = _run_mode(
        output_dir, "stateful-restart", mode="stateful", components=components, worker=worker,
    )
    if resumed["result"] is None:
        raise RuntimeError("stateful task did not resume")

    sequential = _run_mode(
        output_dir, "stateful-sequential", mode="stateful", components=components, worker=worker,
    )
    if sequential["result"] is None:
        raise RuntimeError("sequential stateful baseline did not complete")

    resumed_evidence = resumed["service"].store.get_evidence(resumed["request_hash"])["evidence"]
    sequential_evidence = sequential["service"].store.get_evidence(sequential["request_hash"])["evidence"]
    stateless_evidence = stateless["service"].store.get_evidence(stateless["request_hash"])["evidence"]
    resumed_chunks = resumed["service"].store.get_chunks(resumed["task_id"])
    sequential_chunks = sequential["service"].store.get_chunks(sequential["task_id"])
    resumed_state_hashes = [resumed["service"].store.get_chunk(resumed["task_id"], c["chunk_id"])["state_out_hash"] for c in resumed_chunks]
    sequential_state_hashes = [sequential["service"].store.get_chunk(sequential["task_id"], c["chunk_id"])["state_out_hash"] for c in sequential_chunks]
    hashes_match = (
        resumed_evidence["output_hashes"] == sequential_evidence["output_hashes"]
        and resumed_state_hashes == sequential_state_hashes
    )
    parity = stateless_evidence["factor_values"] == resumed_evidence["factor_values"]
    if not no_partial_evidence or not hashes_match or not parity:
        raise AssertionError("restart/stateful/stateless acceptance parity failed")

    report = {
        "status": "PASS", "protocol_version": "2.0", "formal_inference_status": "BLOCKED",
        "archive_url": ARCHIVE_URL, "archive_sha256": archive_sha256,
        "registered_dataset_id": prepared["registered"]["manifest"]["dataset_id"],
        "registered_manifest_sha256": prepared["registered"]["manifest_sha256"],
        "dataset_manifest_sha256": authorized.canonical_request_record["dataset_manifest_sha256"],
        "dataset_fingerprint": authorized.canonical_request_record["dataset_fingerprint"],
        "worker_image": image, "evaluation_steps": stateless_plan.total_steps,
        "chunk_count": len(stateless_plan.chunks), "chunk_plan_hash": stateless_plan.plan_hash,
        "chunk_input_hashes": [chunk.input_hash for chunk in stateless_plan.chunks],
        "no_partial_evidence_after_forced_restart": no_partial_evidence,
        "stateful_output_hashes_match_after_restart": hashes_match,
        "stateless_stateful_factor_values_match": parity,
        "stateful_output_hashes": resumed_evidence["output_hashes"],
        "stateful_checkpoint_hashes": resumed_state_hashes,
        "stateless_evidence_hash": stateless["result"]["evidence_hash"],
        "stateful_evidence_hash": resumed["result"]["evidence_hash"],
        "runtime_seconds": {
            "stateless": stateless["elapsed_seconds"],
            "stateful_resume_phase": resumed["elapsed_seconds"],
            "stateful_sequential": sequential["elapsed_seconds"],
        },
        "report": resumed_evidence["report"],
    }
    result_path = output_dir / "acceptance-v2.json"
    result_path.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def _blocked_result(mode, run, archive_sha256, plan):
    return {
        "status": "EXECUTION_BLOCKED", "blocked_mode": mode,
        "task_status": run["service"].get_task_v2(run["task_id"])["status"],
        "archive_sha256": archive_sha256, "evaluation_steps": plan.total_steps,
        "formal_inference_status": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--image", default=os.environ.get("ALPHA_LAB_WORKER_IMAGE"))
    args = parser.parse_args(argv)
    report = run_acceptance(args.archive, args.checksum, args.output_dir, image=args.image)
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
