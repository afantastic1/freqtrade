"""Strict, additive wire contract for Python Factor Execution Protocol v2."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alpha_lab_service import PROTOCOL_VERSION_V2
from alpha_lab_service.protocol import MAX_SOURCE_BYTES

V2_PROTOCOL_VERSION = PROTOCOL_VERSION_V2


class FactorServiceLimitsV2(BaseModel):
    """Immutable total-work and per-container budgets owned by the service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["factor-service-v2-limits-v1"] = "factor-service-v2-limits-v1"
    max_steps_per_chunk: int = 512
    max_total_steps: int = 100_000
    max_input_bytes_per_chunk: int = 16 * 1024 * 1024
    max_total_input_bytes: int = 512 * 1024 * 1024
    max_output_bytes_per_chunk: int = 4 * 1024 * 1024
    max_total_output_bytes: int = 256 * 1024 * 1024
    max_state_bytes: int = 1024 * 1024
    max_total_worker_wall_seconds: int = 1_800
    max_attempts_per_chunk: int = 3


V2_LIMITS = FactorServiceLimitsV2()


class FactorRequestV2(BaseModel):
    """Caller declaration; authorization and runtime configuration stay server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research_request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    source: str
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=64)
    worker_input_schema_id: str = Field(min_length=1, max_length=64)
    worker_input_schema_version: str = Field(min_length=1, max_length=32)
    execution_mode: Literal["stateless", "stateful"]
    state_schema_id: str | None = Field(default=None, min_length=1, max_length=64)
    state_schema_version: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, value: Any) -> str:
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("source must be UTF-8") from exc
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source must be non-empty UTF-8 text")
        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds byte limit")
        return value

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if not key or len(key) > 64 or item is None or isinstance(item, (list, dict)):
                raise ValueError("parameters must be named JSON scalar values")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("parameter floats must be finite")
        return value

    @model_validator(mode="after")
    def validate_state_contract(self) -> "FactorRequestV2":
        has_state_schema = self.state_schema_id is not None and self.state_schema_version is not None
        if (self.state_schema_id is None) != (self.state_schema_version is None):
            raise ValueError("state schema id and version must be supplied together")
        if (self.execution_mode == "stateful") != has_state_schema:
            raise ValueError("stateful mode requires a state schema; stateless mode forbids one")
        return self


def canonical_request_hash_v2(
    request: FactorRequestV2,
    *,
    research_request_sha256: str,
    campaign_policy_sha256: str,
    dataset_manifest_sha256: str,
    dataset_fingerprint: str,
    worker_image_digest: str,
    evaluator_identity: str,
    protocol_version: str = V2_PROTOCOL_VERSION,
    limits_version: str = V2_LIMITS.version,
) -> str:
    """Hash declarations and trusted, immutable execution identity as canonical JSON."""
    for value in (research_request_sha256, campaign_policy_sha256, dataset_manifest_sha256, dataset_fingerprint):
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("identity hashes must be lowercase SHA-256 values")
    payload = {
        "protocol_version": protocol_version,
        "limits_version": limits_version,
        "request": request.model_dump(mode="json"),
        "research_request_sha256": research_request_sha256,
        "campaign_policy_sha256": campaign_policy_sha256,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "dataset_fingerprint": dataset_fingerprint,
        "worker_image_digest": worker_image_digest,
        "evaluator_identity": evaluator_identity,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
