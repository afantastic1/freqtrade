
from copy import deepcopy

import pytest

from alpha_lab.evaluation.evidence import build_evidence_report


def evidence_inputs(candidate_name="volume_divergence"):
    return {
        "run_id": "run-001",
        "candidate": {
            "name": candidate_name,
            "source_hash": "a" * 64,
            "parameters": {"window": 12},
            "snapshot_path": "candidate_snapshot/volume_divergence/factor.py",
        },
        "factor_evaluation": {
            "volume_divergence_window_12": {
                "quality": {"valid_ratio": 0.99},
                "overall": {"ic": -0.03, "rank_ic": -0.05},
                "stability": {
                    "mean_monthly_rank_ic": -0.04,
                    "valid_months": 24,
                },
            }
        },
        "model_evaluation": {
            "model": {"auc": 0.53, "logloss": 0.69, "brier": 0.249}
        },
        "incremental_evaluation": {
            "factor_similarity": {
                "momentum_periods_4": {
                    "n": 26281,
                    "pearson": 0.808487,
                    "spearman": 0.953704,
                }
            },
            "baseline": {"auc": 0.526},
            "candidate": {"auc": 0.521},
            "combined": {"auc": 0.530},
            "incremental": {
                "auc": 0.004,
                "logloss": 0.00057,
                "brier": 0.00036,
            },
            "stability": {
                "monthly": [{"month": "2024-01", "auc_improvement": 0.001}],
                "bootstrap": {
                    "block_24": {"positive_ratio": 0.61, "samples": 1000}
                },
                "robust_positive": False,
            },
            "sample_alignment": {
                "common_eligible_rows": 30000,
                "prediction_rows": 12000,
            },
        },
        "evaluation_mode": "incremental",
        "incremental_status": "evaluated",
        "baseline": {
            "source": "candidate_cli_default",
            "factors": [{"feature_column": "momentum_periods_4"}],
        },
        "data": {
            "symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "range": {
                "start": "2022-01-01",
                "end": "2025-01-01",
                "test_start": "2023-01-01",
                "test_end": "2025-01-01",
            },
            "rows": 30000,
            "label_horizon": 4,
        },
        "model": {
            "name": "logistic",
            "train_months": 12,
            "retrain_months": 1,
            "walk_forward": True,
        },
        "bootstrap": {"samples": 1000, "block_sizes": [24, 72, 168]},
    }


def test_evidence_report_preserves_observations_and_provenance():
    report = build_evidence_report(**evidence_inputs())

    assert report["schema_version"] == 1
    assert report["run_id"] == "run-001"
    assert report["candidate"]["source_hash"] == "a" * 64
    assert report["factor_evaluation"]["volume_divergence_window_12"][
        "overall"
    ] == {"ic": -0.03, "rank_ic": -0.05}
    assert report["model_evaluation"]["model"]["auc"] == 0.53
    assert report["incremental_evaluation"]["factor_similarity"][
        "momentum_periods_4"
    ]["spearman"] == 0.953704
    assert report["incremental_evaluation"]["incremental"]["logloss"] == 0.00057
    assert report["incremental_evaluation"]["stability"]["bootstrap"][
        "block_24"
    ]["positive_ratio"] == 0.61
    context = report["evaluation_context"]
    assert context["data"]["range"]["test_start"] == "2023-01-01"
    assert context["model"]["walk_forward"] is True
    assert context["bootstrap"]["block_sizes"] == [24, 72, 168]
    assert context["incremental_status"] == "evaluated"

    serialized = repr(report)
    for forbidden in (
        "classification",
        "suggested_action",
        "policy_version",
        "interpretation",
        "HIGH_REDUNDANCY",
        "WEAK_INCREMENTAL_EVIDENCE",
        "price-location proxy",
    ):
        assert forbidden not in serialized


def test_candidate_name_cannot_change_evidence_policy_or_content():
    first = build_evidence_report(**evidence_inputs("intrabar_pressure_reversal"))
    second = build_evidence_report(**evidence_inputs("unrelated_candidate"))

    first_without_identity = deepcopy(first)
    second_without_identity = deepcopy(second)
    first_without_identity["candidate"].pop("name")
    second_without_identity["candidate"].pop("name")

    assert first_without_identity == second_without_identity


def test_evidence_report_requires_run_and_candidate_source_identity():
    missing_run = evidence_inputs()
    missing_run["run_id"] = ""
    with pytest.raises(ValueError, match="run_id"):
        build_evidence_report(**missing_run)

    missing_hash = evidence_inputs()
    missing_hash["candidate"].pop("source_hash")
    with pytest.raises(ValueError, match="source_hash"):
        build_evidence_report(**missing_hash)


def test_non_incremental_evidence_keeps_explicit_not_evaluated_status():
    inputs = evidence_inputs()
    inputs.update({
        "incremental_evaluation": None,
        "evaluation_mode": "single_factor",
        "incremental_status": "not_evaluated",
        "baseline": None,
    })

    report = build_evidence_report(**inputs)

    assert report["incremental_evaluation"] is None
    assert report["evaluation_context"]["incremental_status"] == "not_evaluated"
    assert report["evaluation_context"]["baseline"] is None
