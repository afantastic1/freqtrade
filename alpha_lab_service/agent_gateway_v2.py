"""Narrow Agent-facing facade over trusted Campaign, Catalog, and Factor services."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from types import MappingProxyType
from typing import Any, Literal

from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_service.isolation_v2 import IsolationVerifierV2, verify_agent_execution_readiness
from alpha_lab_service.protocol_v2 import FactorRequestV2
from alpha_lab_service.protocol import MAX_SOURCE_BYTES


_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_HOST_PATH = re.compile(r"(?<![\w])/(?:home|tmp|mnt|var|etc|root|opt|srv|workspace|run|dev)/[^\s,;]+")
_SENSITIVE_KEYS = frozenset({
    "source", "source_code", "source_path", "path", "paths", "local_path",
    "host_path", "store_path", "catalog_root", "source_root", "root_path",
})


def _safe_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            str(key): _safe_json(item, depth=depth + 1)
            for key, item in value.items()
            if str(key).casefold() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth=depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        if _HOST_PATH.search(value):
            return None
        return value[:8192]
    if value is None or type(value) in {bool, int, float}:
        return value
    return None


def _fits_response(value: dict, limit: int) -> bool:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= limit


class AgentResearchGatewayV2:
    """Expose only request-bound source submission and bounded research reads."""

    def __init__(
        self,
        *,
        request_registry,
        coverage_gateway,
        factor_service,
        isolation_verifier: IsolationVerifierV2 | None,
        input_schema_registry: Mapping[tuple[str, tuple[str, ...]], tuple[str, str, str, str]],
    ):
        if not callable(getattr(request_registry, "create", None)) or not callable(
            getattr(request_registry, "get", None)
        ):
            raise ValueError("trusted ResearchRequest registry is incomplete")
        if not callable(getattr(coverage_gateway, "get_data_coverage", None)):
            raise ValueError("trusted Catalog coverage gateway is unavailable")
        if any(not callable(getattr(factor_service, name, None)) for name in (
            "submit_factor_v2", "run_factor_v2", "get_task_v2", "get_history_v2",
        )):
            raise ValueError("trusted Factor Service v2 is incomplete")
        if not isinstance(input_schema_registry, Mapping):
            raise ValueError("trusted input schema registry must be a mapping")
        bindings = {}
        for key, value in input_schema_registry.items():
            if (not isinstance(key, tuple) or len(key) != 2 or not isinstance(key[0], str)
                    or not isinstance(key[1], tuple) or not all(isinstance(item, str) for item in key[1])
                    or not isinstance(value, tuple) or len(value) != 4
                    or not all(isinstance(item, str) and item for item in value)):
                raise ValueError("invalid trusted input schema binding")
            bindings[(key[0], tuple(sorted(key[1])))] = value
        self.request_registry = request_registry
        self.coverage_gateway = coverage_gateway
        self.factor_service = factor_service
        self.isolation_verifier = isolation_verifier
        self.input_schema_registry = MappingProxyType(bindings)

    def get_data_coverage(self, *, dataset_key: str, start: str, end: str) -> dict:
        result = self.coverage_gateway.get_data_coverage(
            dataset_key=dataset_key, start=start, end=end
        )
        safe = _safe_json(result)
        if not isinstance(safe, dict) or not _fits_response(safe, _MAX_RESPONSE_BYTES):
            return {"status": "UNAVAILABLE", "error_code": "COVERAGE_RESPONSE_INVALID"}
        return safe

    def create_research_request_v2(self, payload: Mapping[str, object]) -> dict:
        record = self.request_registry.create(payload)
        request = ResearchRequestV2.from_dict(record, self.factor_service.policy_registry)
        return {
            "status": "AUTHORIZED",
            "request_id": request.request_id,
            "research_request_sha256": request.sha256(),
            "campaign_id": request.campaign_id,
            "campaign_policy_sha256": request.campaign_policy_sha256,
            "start": request.start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "end": request.end.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "symbols": list(request.symbols),
            "timeframe": request.timeframe,
            "mode": request.mode,
            "data_types": list(request.data_types),
            "split": request.split,
        }

    def submit_factor_v2(
        self,
        *,
        research_request_id: str,
        source: str,
        params: dict | None,
        execution_mode: Literal["stateless", "stateful"],
    ) -> dict:
        raw_request = self.request_registry.get(research_request_id)
        policy_registry = getattr(self.factor_service, "policy_registry", None)
        if policy_registry is None:
            raise ValueError("trusted Campaign policy registry is unavailable")
        request = ResearchRequestV2.from_dict(raw_request, policy_registry)
        binding = self.input_schema_registry.get(
            (request.mode, tuple(sorted(request.data_types)))
        )
        if binding is None:
            raise ValueError("no trusted Worker schema is registered for this request")
        input_schema_id, input_schema_version, state_schema_id, state_schema_version = binding
        factor_request = FactorRequestV2.model_validate({
            "research_request_id": request.request_id,
            "source": source,
            "params": params if params is not None else {},
            "worker_input_schema_id": input_schema_id,
            "worker_input_schema_version": input_schema_version,
            "execution_mode": execution_mode,
            "state_schema_id": state_schema_id if execution_mode == "stateful" else None,
            "state_schema_version": state_schema_version if execution_mode == "stateful" else None,
        })
        if len(factor_request.source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds byte limit")
        result = self.factor_service.submit_factor_v2(factor_request.model_dump(mode="json"))
        if not isinstance(result, dict):
            return {"status": "UNAVAILABLE", "error_code": "TASK_SUBMISSION_INVALID"}
        response = {key: result[key] for key in ("task_id", "request_hash", "status") if key in result}
        safe = _safe_json(response)
        if not isinstance(safe, dict) or not _fits_response(safe, 4096):
            return {"status": "UNAVAILABLE", "error_code": "TASK_SUBMISSION_INVALID"}
        return safe

    async def run_factor_v2(self, task_id: str) -> dict:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            return {"status": "NOT_FOUND", "error_code": "TASK_NOT_FOUND"}
        task = self.factor_service.get_task_v2(task_id)
        if task is None:
            return {"status": "NOT_FOUND", "error_code": "TASK_NOT_FOUND"}
        if not verify_agent_execution_readiness(
            self.isolation_verifier,
            worker_image_digest=self.factor_service.worker_identity,
        ):
            return {
                "task_id": task_id,
                "status": "EXECUTION_BLOCKED",
                "error_code": "ISOLATION_NOT_VERIFIED",
            }
        result = await self.factor_service.run_factor_v2(task_id)
        safe = _safe_json(result)
        if not isinstance(safe, dict) or not _fits_response(safe, _MAX_RESPONSE_BYTES):
            return {"task_id": task_id, "status": "UNAVAILABLE", "error_code": "TASK_RESULT_INVALID"}
        return safe

    def get_task_status(self, task_id: str) -> dict:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            return {"status": "NOT_FOUND", "error_code": "TASK_NOT_FOUND"}
        task = self.factor_service.get_task_v2(task_id)
        if task is None:
            return {"status": "NOT_FOUND", "error_code": "TASK_NOT_FOUND"}
        fields = ("task_id", "status", "request_hash", "plan_hash", "chunk_count", "created_at")
        return {key: task[key] for key in fields if key in task}

    def get_task_report(self, task_id: str) -> dict:
        status = self.get_task_status(task_id)
        if status.get("status") == "NOT_FOUND":
            return status
        if status.get("status") != "COMPLETED":
            return {"task_id": task_id, "status": status["status"]}
        store = getattr(self.factor_service, "store", None)
        get_evidence = getattr(store, "get_evidence", None)
        if not callable(get_evidence):
            return {"task_id": task_id, "status": "UNAVAILABLE", "error_code": "EVIDENCE_STORE_UNAVAILABLE"}
        evidence_row = get_evidence(status.get("request_hash"))
        if (not isinstance(evidence_row, dict) or evidence_row.get("task_id") != task_id
                or evidence_row.get("run_id") != status.get("request_hash")):
            return {"task_id": task_id, "status": "UNAVAILABLE", "error_code": "EVIDENCE_MISSING"}
        evidence = evidence_row.get("evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("report"), dict):
            return {"task_id": task_id, "status": "UNAVAILABLE", "error_code": "EVIDENCE_INVALID"}
        response = {
            "task_id": task_id,
            "run_id": evidence_row["run_id"],
            "status": evidence.get("status"),
            "evidence_hash": evidence_row.get("evidence_hash"),
            "report": _safe_json(evidence["report"]),
            "formal_inference_status": "BLOCKED",
        }
        if not _fits_response(response, _MAX_REPORT_BYTES):
            return {"task_id": task_id, "status": "UNAVAILABLE", "error_code": "REPORT_TOO_LARGE"}
        return response

    def get_history_v2(self, limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history limit outside allowed range")
        history = self.factor_service.get_history_v2(limit)
        if not isinstance(history, dict) or not isinstance(history.get("entries"), list):
            return {"status": "UNAVAILABLE", "error_code": "HISTORY_INVALID"}
        entries = []
        for entry in history["entries"][:limit]:
            if not isinstance(entry, dict):
                continue
            row = {key: entry[key] for key in (
                "kind", "identity", "created_at", "store_id", "protocol_version", "payload"
            ) if key in entry}
            entries.append(_safe_json(row))
        response = {
            "entries": entries,
            "source_status": _safe_json(history.get("source_status", {})),
            "source_errors": _safe_json(history.get("source_errors", {})),
        }
        if not _fits_response(response, _MAX_RESPONSE_BYTES):
            return {"status": "UNAVAILABLE", "error_code": "HISTORY_TOO_LARGE"}
        return response
