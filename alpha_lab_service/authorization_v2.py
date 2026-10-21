"""Resolve v2 factor declarations into a trusted ResearchRequest and dataset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping, Protocol

import pandas as pd

from alpha_lab.data.build_products import DatasetBuildResultV2
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab.data.universe import DynamicUniverse, HistoricalUniverse
from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyRegistry
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY, WorkerInputSchemaV1
from alpha_lab_service.protocol_v2 import FactorRequestV2, V2_LIMITS, canonical_request_hash_v2
from alpha_lab_service.worker_protocol_v2 import validate_state_schema

_IMAGE_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|.+@sha256:[0-9a-f]{64})$")


class ResearchRequestRegistryV1(Protocol):
    """Trusted index that binds a caller-visible request ID to immutable request bytes."""

    def get(self, research_request_id: str) -> Mapping[str, object]: ...


class MappingResearchRequestRegistryV1:
    """Read-only registry adapter for request records loaded by the trusted service factory."""

    def __init__(self, requests: Mapping[str, Mapping[str, object]]):
        self._requests = MappingProxyType({key: dict(value) for key, value in requests.items()})

    def get(self, research_request_id: str) -> Mapping[str, object]:
        try:
            return dict(self._requests[research_request_id])
        except KeyError as exc:
            raise ValueError("unknown research request") from exc


@dataclass(frozen=True)
class AuthorizedFactorRunV2:
    research_request: ResearchRequestV2
    campaign_policy_sha256: str
    dataset: DatasetBuildResultV2
    input_schema: WorkerInputSchemaV1
    evaluation_timestamps: tuple[pd.Timestamp, ...]
    identity_hash: str
    canonical_request_record: dict
    worker_image_digest: str
    evaluator_identity: str


def _canonical_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _frame_hash(frame: pd.DataFrame) -> str:
    columns = [(str(name), str(frame[name].dtype)) for name in frame.columns]
    values = pd.util.hash_pandas_object(frame, index=True, categorize=True).values.tobytes()
    digest = hashlib.sha256()
    digest.update(json.dumps(columns, separators=(",", ":")).encode("utf-8"))
    digest.update(values)
    return digest.hexdigest()


def _dataset_identity(dataset: DatasetBuildResultV2) -> tuple[str, str]:
    manifest_hash = _canonical_hash(dataset.manifest)
    fingerprint = _canonical_hash({
        "manifest_sha256": manifest_hash,
        "worker_input_sha256": _frame_hash(dataset.worker_input.frame),
        "evaluator_labels_sha256": _frame_hash(dataset.evaluator_labels.frame),
    })
    return manifest_hash, fingerprint


def _evaluation_timestamps(dataset: DatasetBuildResultV2, request: ResearchRequestV2) -> tuple[pd.Timestamp, ...]:
    worker = dataset.worker_input.frame
    if "decision_time" not in worker:
        raise ValueError("worker input must provide decision_time scheduling metadata")
    values = pd.to_datetime(worker.decision_time, utc=True, errors="raise")
    start, end = pd.Timestamp(request.start), pd.Timestamp(request.end)
    selected = values[(values >= start) & (values < end)]
    timestamps = tuple(pd.Timestamp(value).tz_convert("UTC") for value in selected.drop_duplicates().sort_values())
    if not timestamps:
        raise ValueError("authorized ResearchRequest has no evaluation timestamps")
    if len(timestamps) > V2_LIMITS.max_total_steps:
        raise ValueError("authorized ResearchRequest exceeds total evaluation step limit")
    return timestamps


def resolve_authorized_run(
    request: FactorRequestV2 | Mapping[str, object],
    *,
    research_request_registry: ResearchRequestRegistryV1,
    policy_registry: CampaignPolicyRegistry,
    dataset_builder,
    dataset_options: DatasetBuildOptionsV1,
    worker_identity: str,
    evaluator_identity: str,
    universe: DynamicUniverse | HistoricalUniverse | None = None,
) -> AuthorizedFactorRunV2:
    """Resolve authority, build the pinned dataset, and hash complete execution identity.

    `research_request_registry`, `dataset_options`, `worker_identity`, and `universe`
    are trusted service-factory dependencies; none comes from the factor request.
    """
    factor_request = (request if isinstance(request, FactorRequestV2)
                     else FactorRequestV2.model_validate(request))
    raw_request = research_request_registry.get(factor_request.research_request_id)
    if raw_request.get("request_id") != factor_request.research_request_id:
        raise ValueError("research request registry identity mismatch")
    trusted_request = ResearchRequestV2.from_dict(raw_request, policy_registry)
    policy = policy_registry.get(
        trusted_request.campaign_id, trusted_request.policy_version, trusted_request.campaign_policy_sha256
    )
    if not _IMAGE_DIGEST.fullmatch(worker_identity):
        raise ValueError("Worker identity must be pinned by an immutable image digest")
    if not evaluator_identity or len(evaluator_identity) > 128:
        raise ValueError("evaluator identity is required and bounded")
    schema = INPUT_SCHEMA_REGISTRY.get(
        factor_request.worker_input_schema_id, factor_request.worker_input_schema_version
    )
    if factor_request.execution_mode == "stateful":
        validate_state_schema(factor_request.state_schema_id, factor_request.state_schema_version)

    dataset = dataset_builder.build(trusted_request, options=dataset_options, universe=universe)
    schema.validate_fields(dataset.worker_input.frame.columns)
    timestamps = _evaluation_timestamps(dataset, trusted_request)
    manifest_hash, dataset_fingerprint = _dataset_identity(dataset)
    schema_identity = [
        {
            "name": name, "logical_type": field.logical_type, "units": field.units,
            "source_data_type": field.source_data_type,
            "availability_rule": field.availability_rule,
            "null_policy": field.null_policy, "shape": field.shape,
        }
        for name, field in sorted(schema.fields.items())
    ]
    schema_hash = _canonical_hash(schema_identity)
    identity_hash = canonical_request_hash_v2(
        factor_request,
        research_request_sha256=trusted_request.sha256(),
        campaign_policy_sha256=policy.sha256(),
        dataset_manifest_sha256=manifest_hash,
        dataset_fingerprint=dataset_fingerprint,
        worker_input_schema_sha256=schema_hash,
        worker_image_digest=worker_identity,
        evaluator_identity=evaluator_identity,
        limits_version=V2_LIMITS.version,
    )
    canonical_record = {
        "factor_request": factor_request.model_dump(mode="json"),
        "research_request": json.loads(trusted_request.canonical_json()),
        "campaign_policy_sha256": policy.sha256(),
        "dataset_manifest_sha256": manifest_hash,
        "dataset_fingerprint": dataset_fingerprint,
        "input_schema": {"id": schema.schema_id, "version": schema.version},
        "worker_input_schema_sha256": schema_hash,
        "worker_image_digest": worker_identity,
        "evaluator_identity": evaluator_identity,
        "limits_version": V2_LIMITS.version,
        "identity_hash": identity_hash,
    }
    return AuthorizedFactorRunV2(
        trusted_request, policy.sha256(), dataset, schema, timestamps,
        identity_hash, canonical_record, worker_identity, evaluator_identity,
    )
