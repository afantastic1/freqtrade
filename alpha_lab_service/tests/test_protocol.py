import pytest

from alpha_lab_service.protocol import (
    FactorOutput,
    FactorRequest,
    canonical_request_hash,
)


def _request(**changes):
    payload = {
        "source": "def compute_factor(history, *, as_of, params):\n    return history\n",
        "hypothesis": "Recent returns have cross-sectional information.",
        "symbols": ["AAA", "BBB"],
        "timeframe": "1d",
        "start": "2022-01-01T00:00:00Z",
        "end": "2024-12-31T00:00:00Z",
        "params": {"lookback": 20, "enabled": True},
        "max_lookback": 60,
    }
    payload.update(changes)
    return payload


def test_request_accepts_source_content_and_has_no_host_path_contract():
    request = FactorRequest.model_validate(_request())
    assert request.source.startswith("def compute_factor")
    assert request.symbols == ["AAA", "BBB"]
    for forbidden in ("path", "image", "command", "mount", "database_path"):
        with pytest.raises(ValueError):
            FactorRequest.model_validate(_request(**{forbidden: "/tmp/input.py"}))


def test_request_rejects_source_over_limit_and_non_scalar_parameters():
    with pytest.raises(ValueError):
        FactorRequest.model_validate(_request(source="x" * (256 * 1024 + 1)))
    with pytest.raises(ValueError):
        FactorRequest.model_validate(_request(params={"lookback": [1, 2]}))
    with pytest.raises(ValueError):
        FactorRequest.model_validate(_request(params={"bad": float("nan")}))


def test_request_rejects_unknown_fields_and_invalid_window():
    with pytest.raises(ValueError):
        FactorRequest.model_validate(_request(arbitrary_worker_arg="sh"))
    with pytest.raises(ValueError):
        FactorRequest.model_validate(_request(end="2021-12-31T00:00:00Z"))


def test_canonical_identity_is_stable_and_binds_runtime():
    request = FactorRequest.model_validate(_request())
    identity = dict(data_fingerprint="data-sha", image_digest="sha256:image", engine_identity="engine-v1")
    first = canonical_request_hash(request, **identity)
    second = canonical_request_hash(FactorRequest.model_validate(_request()), **identity)
    assert first == second
    assert first != canonical_request_hash(request, **{**identity, "image_digest": "sha256:other"})
    assert first != canonical_request_hash(
        request, **identity, statistical_method_identity="other-descriptive-projection"
    )


def test_factor_output_rejects_nonfinite_and_unbounded_diagnostics():
    with pytest.raises(ValueError):
        FactorOutput.model_validate({
            "as_of": "2024-01-01T00:00:00Z",
            "values": {"AAA": float("inf")},
            "diagnostics": {},
            "protocol_version": "1.0",
        })
    with pytest.raises(ValueError):
        FactorOutput.model_validate({
            "as_of": "2024-01-01T00:00:00Z",
            "values": {"AAA": 1.0},
            "diagnostics": {"unstable": float("nan")},
            "protocol_version": "1.0",
        })
    with pytest.raises(ValueError):
        FactorOutput.model_validate({
            "as_of": "2024-01-01T00:00:00Z",
            "values": {"AAA": 1.0},
            "diagnostics": {},
            "protocol_version": "9.9",
        })
    with pytest.raises(ValueError):
        FactorOutput.model_validate({
            "as_of": "2024-01-01T00:00:00Z",
            "values": {"AAA": 1.0},
            "diagnostics": {"text": "x" * 5000},
            "protocol_version": "1.0",
        })
