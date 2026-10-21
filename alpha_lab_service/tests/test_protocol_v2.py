import hashlib
import json

import pytest
from pydantic import ValidationError

from alpha_lab_service.protocol import FactorRequest, canonical_request_hash
from alpha_lab_service.protocol_v2 import (
    FactorRequestV2, V2_LIMITS, canonical_request_hash_v2,
)
from alpha_lab_service import PROTOCOL_VERSION_V2


def valid_request(**changes):
    value = {
        "research_request_id": "research-001",
        "source": "def factor(frame, params): return 1.0",
        "params": {"period": 12},
        "worker_input_schema_id": "ohlcv-v1",
        "worker_input_schema_version": "1",
        "execution_mode": "stateless",
    }
    value.update(changes)
    return value


def test_factor_request_v2_rejects_authority_and_runtime_overrides():
    with pytest.raises(ValidationError):
        FactorRequestV2.model_validate(valid_request(symbols=["ETH/USDT"], image="attacker"))


def test_factor_request_v2_bounds_utf8_source_and_finite_scalars():
    with pytest.raises(ValidationError):
        FactorRequestV2.model_validate(valid_request(source="é" * (128 * 1024 + 1)))
    with pytest.raises(ValidationError):
        FactorRequestV2.model_validate(valid_request(params={"bad": float("nan")}))


def test_factor_request_v2_requires_state_schema_only_for_stateful_mode():
    with pytest.raises(ValidationError):
        FactorRequestV2.model_validate(valid_request(execution_mode="stateful"))
    with pytest.raises(ValidationError):
        FactorRequestV2.model_validate(valid_request(state_schema_id="counter", state_schema_version="1"))
    request = FactorRequestV2.model_validate(valid_request(
        execution_mode="stateful", state_schema_id="counter", state_schema_version="1"
    ))
    assert request.execution_mode == "stateful"


def test_v2_limits_are_immutable_and_versioned():
    assert PROTOCOL_VERSION_V2 == "2.0"
    assert V2_LIMITS.version == "factor-service-v2-limits-v1"
    assert V2_LIMITS.max_steps_per_chunk == 512
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        V2_LIMITS.max_steps_per_chunk = 1


def test_v2_identity_canonicalizes_material_inputs():
    request = FactorRequestV2.model_validate(valid_request())
    kwargs = dict(
        research_request_sha256="a" * 64, campaign_policy_sha256="b" * 64,
        dataset_manifest_sha256="c" * 64, dataset_fingerprint="d" * 64,
        worker_input_schema_sha256="f" * 64,
        worker_image_digest="sha256:" + "e" * 64, evaluator_identity="eval-v1",
    )
    first = canonical_request_hash_v2(request, **kwargs)
    assert first == canonical_request_hash_v2(request, **kwargs)
    assert first != canonical_request_hash_v2(request, **{**kwargs, "evaluator_identity": "eval-v2"})
    assert first != canonical_request_hash_v2(request, **{**kwargs, "worker_input_schema_sha256": "0" * 64})
    assert len(first) == 64


def test_v1_request_hash_behavior_is_unchanged():
    request = FactorRequest.model_validate({
        "source": "def factor(frame, params): return 1.0", "hypothesis": "test",
        "symbols": ["BTC/USDT"], "timeframe": "5m", "start": "2025-01-01T00:00:00Z",
        "end": "2025-01-02T00:00:00Z", "params": {}, "max_lookback": 1,
    })
    actual = canonical_request_hash(request, data_fingerprint="data", image_digest="image", engine_identity="engine")
    assert actual == "42bc011640bc17f22e292701cfbe2c5797217373ad02edc0651b7245fb2c3399"
