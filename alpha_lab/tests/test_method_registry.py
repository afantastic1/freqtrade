from alpha_lab.stats.method_registry import method_entry
from alpha_lab.stats.verdict import (
    INCONCLUSIVE,
    MetricAssessment,
    VerdictPolicy,
    classify_formal_result,
    gate_formal_verdict,
)


def test_unvalidated_method_cannot_create_formal_verdict():
    evidence = {
        "auc": MetricAssessment(
            metric="auc",
            point=0.02,
            ci_lower=0.01,
            ci_upper=0.03,
            mde=0.001,
            practical_threshold=0.005,
            adjusted_p=0.001,
        )
    }
    verdict = classify_formal_result(
        evidence,
        primary_metric="auc",
        method_id="paired-mbb-centered-null-v3",
        policy=VerdictPolicy(),
    )

    assert verdict.label == INCONCLUSIVE
    assert "INFERENCE_METHOD_NOT_APPROVED" in verdict.reasons
    assert method_entry("paired-mbb-centered-null-v3")["status"] == "LEGACY"


def test_unknown_and_adjustment_only_methods_are_not_inference_methods():
    assert method_entry("not-registered") is None
    assert method_entry("scipy-bh-v1")["role"] == "adjustment_only"


def test_method_gate_also_blocks_combined_mechanism_rejection():
    historical = {
        "label": "REJECTED",
        "reasons": ["MECHANISM:REJECTED"],
    }
    gated = gate_formal_verdict(historical, method_id="prereg-a-v3")

    assert gated["label"] == INCONCLUSIVE
    assert "INFERENCE_METHOD_NOT_APPROVED" in gated["reasons"]
