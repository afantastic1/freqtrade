"""Recoverable scheduling and complete-output handoff for Factor Service v2."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time

import pandas as pd

from alpha_lab_service.authorization_v2 import resolve_authorized_run
from alpha_lab_service.chunking_v2 import build_chunk_frames, plan_chunks, validate_chunk_output
from alpha_lab_service.evaluator import evaluate_authorized_factor_output
from alpha_lab_service.protocol_v2 import FactorRequestV2, V2_LIMITS


class ResearchServiceV2:
    """Trusted v2 service. All authority and runtime dependencies are factory-owned."""

    def __init__(self, store, broker, *, research_request_registry, policy_registry,
                 dataset_builder, dataset_options, worker_identity: str,
                 evaluator_identity: str, universe=None, max_steps_per_chunk: int = 512):
        self.store = store
        self.broker = broker
        self.research_request_registry = research_request_registry
        self.policy_registry = policy_registry
        self.dataset_builder = dataset_builder
        self.dataset_options = dataset_options
        self.worker_identity = worker_identity
        self.evaluator_identity = evaluator_identity
        self.universe = universe
        self.max_steps_per_chunk = max_steps_per_chunk
        self._run_lock = asyncio.Lock()

    def _resolve(self, payload):
        run = resolve_authorized_run(
            payload, research_request_registry=self.research_request_registry,
            policy_registry=self.policy_registry, dataset_builder=self.dataset_builder,
            dataset_options=self.dataset_options, worker_identity=self.worker_identity,
            evaluator_identity=self.evaluator_identity, universe=self.universe,
        )
        return run, plan_chunks(run, max_steps_per_chunk=self.max_steps_per_chunk)

    def submit_factor_v2(self, payload: dict) -> dict:
        run, plan = self._resolve(payload)
        task_id = run.identity_hash[:32]
        chunks = [{
            "chunk_id": chunk.chunk_id, "chunk_index": chunk.index,
            "input_hash": chunk.input_hash,
        } for chunk in plan.chunks]
        selected = self.store.create_or_get_task(
            task_id=task_id, request_hash=run.identity_hash,
            request=run.canonical_request_record, plan_hash=plan.plan_hash, chunks=chunks,
        )
        task = self.store.get_task(selected)
        return {"task_id": selected, "request_hash": run.identity_hash, "status": task["status"]}

    async def run_factor_v2(self, task_id: str) -> dict:
        async with self._run_lock:
            record = self.store.get_task_record(task_id)
            if record is None:
                raise KeyError("unknown v2 task")
            run_id = record["request_hash"]
            evidence_row = self.store.get_evidence(run_id)
            if evidence_row:
                evidence = evidence_row["evidence"]
                return {"task_id": task_id, "run_id": run_id, "status": evidence["status"],
                        "evidence_hash": evidence_row["evidence_hash"], "report": evidence["report"]}
            task = self.store.get_task(task_id)
            if task["status"] in {"FAILED", "EXECUTION_BLOCKED", "CANCELLED"}:
                return {"task_id": task_id, "status": task["status"]}
            self.store.recover_interrupted_attempts(task_id)
            task = self.store.get_task(task_id)
            try:
                run, plan = self._resolve(record["request"]["factor_request"])
                if run.identity_hash != run_id or plan.plan_hash != record["plan_hash"]:
                    raise ValueError("trusted task identity or plan changed")
                # Compare the re-derived immutable plan to the persisted plan, including inputs.
                persisted = self.store.get_chunks(task_id)
                expected = [{"chunk_id": chunk.chunk_id, "chunk_index": chunk.index,
                             "plan_hash": plan.plan_hash, "input_hash": chunk.input_hash}
                            for chunk in plan.chunks]
                if persisted != expected:
                    raise ValueError("persisted chunk plan does not match trusted inputs")
            except Exception as exc:
                self.store.append_task_status(task_id, "EXECUTION_BLOCKED", {"category": type(exc).__name__})
                return {"task_id": task_id, "status": "EXECUTION_BLOCKED", "error": type(exc).__name__}

            self.store.append_task_status(task_id, "RUNNING")
            factor_request = FactorRequestV2.model_validate(run.canonical_request_record["factor_request"])
            for chunk in plan.chunks:
                if self.store.get_chunk(task_id, chunk.chunk_id)["status"] == "COMMITTED":
                    continue
                state_in = self.store.get_resume_state(task_id, chunk.chunk_id)
                frames = build_chunk_frames(run, chunk)
                frames[0]["state"] = state_in
                if factor_request.execution_mode == "stateful" and chunk.index > 0:
                    frames[1]["phase"] = "read_only"
                input_bytes = sum(len(json.dumps(
                    frame, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")) + 1 for frame in frames)
                while True:
                    if self.store.get_total_worker_wall_seconds(task_id) >= V2_LIMITS.max_total_worker_wall_seconds:
                        self.store.append_task_status(task_id, "EXECUTION_BLOCKED", {"category": "worker_time_limit"})
                        return {"task_id": task_id, "status": "EXECUTION_BLOCKED"}
                    if (input_bytes > V2_LIMITS.max_input_bytes_per_chunk
                            or self.store.get_total_worker_input_bytes(task_id) + input_bytes
                            > V2_LIMITS.max_total_input_bytes):
                        self.store.append_task_status(task_id, "EXECUTION_BLOCKED", {"category": "worker_input_limit"})
                        return {"task_id": task_id, "status": "EXECUTION_BLOCKED"}
                    attempt = self.store.begin_chunk_attempt(
                        task_id, chunk.chunk_id, input_hash=chunk.input_hash, state_in=state_in,
                        input_bytes=input_bytes,
                    )
                    started = time.monotonic()
                    try:
                        runner = self.broker.run_chunk
                        if inspect.iscoroutinefunction(runner):
                            output = await runner(task_id, chunk.chunk_id, frames)
                        else:
                            output = await asyncio.to_thread(runner, task_id, chunk.chunk_id, frames)
                        elapsed = time.monotonic() - started
                        results = validate_chunk_output(run, chunk, frames, output)
                        state_out = output[-1]["state"] if factor_request.execution_mode == "stateful" else None
                        if self.store.get_total_worker_wall_seconds(task_id) + elapsed > V2_LIMITS.max_total_worker_wall_seconds:
                            self.store.record_chunk_failure(
                                task_id, chunk.chunk_id, attempt, retryable=False, blocked=True,
                                elapsed_seconds=elapsed, diagnostics={"category": "worker_time_limit"},
                            )
                            return {"task_id": task_id, "status": "EXECUTION_BLOCKED"}
                        self.store.commit_chunk(task_id, chunk.chunk_id, attempt, output=results,
                                                state_out=state_out, elapsed_seconds=elapsed)
                        break
                    except Exception as exc:
                        elapsed = time.monotonic() - started
                        retryable = isinstance(exc, TimeoutError) or (
                            isinstance(exc, RuntimeError) and any(word in str(exc).lower()
                                for word in ("unavailable", "capacity", "timeout"))
                        )
                        over_limit = (self.store.get_total_worker_wall_seconds(task_id) + elapsed
                                      > V2_LIMITS.max_total_worker_wall_seconds)
                        outcome = self.store.record_chunk_failure(
                            task_id, chunk.chunk_id, attempt, retryable=retryable and not over_limit,
                            blocked=(not retryable or over_limit), elapsed_seconds=elapsed,
                            diagnostics={"category": "worker_time_limit" if over_limit else type(exc).__name__},
                        )
                        if outcome == "RETRYABLE_FAILURE":
                            self.store.append_task_status(task_id, "RUNNING")
                            continue
                        return {"task_id": task_id, "status": outcome}

            try:
                rows = []
                output_hashes = []
                for chunk in plan.chunks:
                    output_hashes.append(self.store.get_chunk(task_id, chunk.chunk_id)["output_hash"])
                    for result in self.store.get_committed_output(task_id, chunk.chunk_id):
                        timestamp = pd.Timestamp(result["as_of"]).tz_convert("UTC")
                        for symbol in run.research_request.symbols:
                            value = result["values"][symbol]
                            rows.append((timestamp, symbol, float(value)))
                index = pd.MultiIndex.from_tuples(
                    [(stamp, symbol) for stamp, symbol, _ in rows], names=["open_time", "symbol"]
                )
                factor = pd.Series([value for _, _, value in rows], index=index, dtype=float)
                if len(factor) != plan.total_steps or not factor.index.is_unique:
                    raise ValueError("merged Worker output is incomplete or duplicated")
                report = evaluate_authorized_factor_output(factor, run, identity=run_id)
                evidence = {
                    "status": report["status"], "protocol_version": "2.0",
                    "request_hash": run_id,
                    "source_sha256": hashlib.sha256(factor_request.source.encode("utf-8")).hexdigest(),
                    "dataset_manifest_sha256": run.canonical_request_record["dataset_manifest_sha256"],
                    "dataset_fingerprint": run.canonical_request_record["dataset_fingerprint"],
                    "worker_image": run.worker_image_digest, "evaluator_identity": run.evaluator_identity,
                    "output_hashes": output_hashes,
                    "factor_values": [{"open_time": stamp.isoformat(), "symbol": symbol, "value": value}
                                      for stamp, symbol, value in rows],
                    "report": report,
                    "formal_inference_status": "BLOCKED",
                }
                evidence_hash = self.store.complete_with_evidence(task_id=task_id, run_id=run_id, evidence=evidence)
                return {"task_id": task_id, "run_id": run_id, "status": report["status"],
                        "evidence_hash": evidence_hash, "report": report}
            except Exception as exc:
                # All chunks may be committed, but evaluator/Evidence failures remain
                # resumable until their terminal classification is persisted.
                status = self.store.get_task(task_id)["status"]
                if status == "RUNNING":
                    self.store.append_task_status(task_id, "FAILED", {"category": type(exc).__name__})
                return {"task_id": task_id, "status": "FAILED", "error": type(exc).__name__}

    def get_task_v2(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)
