"""Bounded task lifecycle and trusted evaluation orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Callable

import pandas as pd

from alpha_lab_service import PROTOCOL_VERSION
from alpha_lab_service.data_feed import validate_worker_steps
from alpha_lab_service.evaluator import evaluate_factor_output
from alpha_lab_service.protocol import FactorOutput, FactorRequest, canonical_request_hash
from alpha_lab.stats.method_registry import METHOD_REGISTRY, REGISTRY_VERSION
from alpha_lab.version import ENGINE_VERSION

MAX_STEPS_PER_TASK = 512
FORMAL_INFERENCE_STATUS = "BLOCKED"
STATISTICAL_METHOD_IDENTITY = json.dumps({
    "registry_version": REGISTRY_VERSION,
    "formal_inference_status": FORMAL_INFERENCE_STATUS,
    "inference_methods": {
        method_id: entry["status"] for method_id, entry in METHOD_REGISTRY.items()
        if entry["role"] == "inference"
    },
    "descriptive_methods": ["scipy-pearson-point-v1", "scipy-spearman-point-v1"],
}, sort_keys=True, separators=(",", ":"))
DATA_AVAILABILITY_SEMANTICS = "UTC;available_time<=as_of;open_time<=as_of;OHLCV-only"


class ResearchService:
    def __init__(self, store, broker, *, frame_provider: Callable, labels_provider: Callable,
                 data_fingerprint: str, image_digest: str, engine_identity: str,
                 baseline_provider: Callable | None = None,
                 statistical_method_identity: str = STATISTICAL_METHOD_IDENTITY,
                 data_availability_semantics: str = DATA_AVAILABILITY_SEMANTICS):
        self.store = store
        self.broker = broker
        self.frame_provider = frame_provider
        self.labels_provider = labels_provider
        self.baseline_provider = baseline_provider
        self.data_fingerprint = data_fingerprint
        self.image_digest = image_digest
        self.engine_identity = engine_identity
        self.statistical_method_identity = statistical_method_identity
        self.data_availability_semantics = data_availability_semantics
        self._run_lock = asyncio.Lock()

    def submit_factor(self, payload: dict) -> dict:
        request = FactorRequest.model_validate(payload)
        identity = canonical_request_hash(
            request, data_fingerprint=self.data_fingerprint,
            image_digest=self.image_digest, engine_identity=self.engine_identity,
            statistical_method_identity=self.statistical_method_identity,
            data_availability_semantics=self.data_availability_semantics,
        )
        task_id = identity[:32]
        serialized = request.model_dump(mode="json")
        selected = self.store.append_task(task_id=task_id, request_hash=identity, request=serialized)
        task = self.store.get_task(selected)
        return {"task_id": selected, "request_hash": identity, "status": task["status"]}

    async def run_factor(self, task_id: str) -> dict:
        async with self._run_lock:
            task = self.store.get_task(task_id)
            if task is None:
                raise KeyError("unknown task")
            run_id = task["request_hash"]
            previous = self.store.get_evidence(run_id)
            if previous:
                return {"task_id": task_id, "run_id": run_id, "status": previous["evidence"]["status"],
                        "evidence_hash": previous["evidence_hash"], "report": previous["evidence"]["report"]}
            if task["status"] in {"RUNNING", "TIMED_OUT", "EXECUTION_BLOCKED", "FAILED"}:
                raise RuntimeError("task has already been attempted; submit a new identity")
            request = FactorRequest.model_validate(task["request"])
            self.store.append_task_status(task_id, "RUNNING")
            try:
                steps = self.frame_provider(request)
                if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS_PER_TASK:
                    raise ValueError("bounded as-of step list required")
                validate_worker_steps(
                    steps, symbols=request.symbols, start=request.start, end=request.end,
                    max_lookback=request.max_lookback,
                )
                init = {
                    "type": "init", "protocol_version": PROTOCOL_VERSION,
                    "source": request.source, "symbols": request.symbols,
                    "params": request.params, "seed": 0,
                }
                frames = [init, *steps, {"type": "end"}]
                runner = self.broker.run_frames
                if inspect.iscoroutinefunction(runner):
                    results = await runner(task_id, frames)
                else:
                    results = await asyncio.to_thread(runner, task_id, frames)
                outputs = results[1:]
                if len(outputs) != len(steps):
                    raise ValueError("Worker output count did not match as-of steps")
                rows = []
                output_hashes = []
                for step, result in zip(steps, outputs, strict=True):
                    parsed = FactorOutput.model_validate(result)
                    if parsed.as_of.isoformat() != pd.Timestamp(step.get("as_of")).tz_convert("UTC").isoformat() or set(parsed.values) != set(request.symbols):
                        raise ValueError("Worker output violates the factor protocol")
                    timestamp = pd.Timestamp(parsed.as_of)
                    if timestamp.tzinfo is None:
                        raise ValueError("Worker output timestamp is not timezone-aware")
                    for symbol, value in parsed.values.items():
                        rows.append((timestamp.tz_convert("UTC"), symbol, float(value)))
                    output_hashes.append(result.get("output_hash", ""))
                index = pd.MultiIndex.from_tuples(
                    [(timestamp, symbol) for timestamp, symbol, _ in rows], names=["open_time", "symbol"]
                )
                factor = pd.Series([value for _, _, value in rows], index=index, dtype=float)
                labels = self.labels_provider(request, factor.index)
                baseline = self.baseline_provider(request, factor.index) if self.baseline_provider else None
                report = evaluate_factor_output(factor, labels, baseline=baseline, identity=run_id)
                evidence = {
                    "status": report["status"],
                    "protocol_version": PROTOCOL_VERSION,
                    "request_hash": task["request_hash"],
                    "source_sha256": hashlib.sha256(request.source.encode("utf-8")).hexdigest(),
                    "data_fingerprint": self.data_fingerprint,
                    "worker_image": self.image_digest,
                    "engine_identity": self.engine_identity,
                    "engine_version": ENGINE_VERSION,
                    "statistical_method_identity": self.statistical_method_identity,
                    "data_availability_semantics": self.data_availability_semantics,
                    "output_hashes": output_hashes,
                    "factor_values": [
                        {"open_time": timestamp.isoformat(), "symbol": symbol, "value": value}
                        for timestamp, symbol, value in rows
                    ],
                    "report": report,
                }
                evidence_hash = self.store.append_evidence(task_id=task_id, run_id=run_id, evidence=evidence)
                self.store.append_task_status(task_id, "COMPLETED", {"evidence_hash": evidence_hash})
                return {"task_id": task_id, "run_id": run_id, "status": report["status"],
                        "evidence_hash": evidence_hash, "report": report}
            except TimeoutError:
                self.store.append_task_status(task_id, "TIMED_OUT")
                return {"task_id": task_id, "status": "TIMED_OUT"}
            except Exception as exc:
                self.store.append_task_status(task_id, "EXECUTION_BLOCKED", {"category": type(exc).__name__})
                return {"task_id": task_id, "status": "EXECUTION_BLOCKED", "error": type(exc).__name__}

    def get_task(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)

    def get_history(self, limit: int = 100) -> list[dict]:
        return self.store.list_history(limit)

    def append_decision(self, *, decision_id: str, run_id: str, decision: dict) -> dict:
        status = decision.get("status")
        if status not in {"EXPLORATORY", "DIAGNOSTIC"}:
            raise ValueError("Python factor decisions are descriptive only")
        digest = self.store.append_decision(decision_id=decision_id, run_id=run_id, decision=decision)
        return {"decision_id": decision_id, "decision_hash": digest, "status": status}
