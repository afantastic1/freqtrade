
from alpha_lab.evaluation.feedback import (
    build_research_feedback,
)


def test_high_redundancy_without_robust_gain():
    report = {
        "factor_similarity": {
            "momentum_periods_4": {
                "n": 26281,
                "pearson": 0.808487,
                "spearman": 0.953704,
            }
        },
        "incremental": {
            "auc": 0.004118,
            "logloss": 0.000571,
            "brier": 0.000367,
        },
        "stability": {
            "robust_positive": False,
        },
    }

    feedback = build_research_feedback(
        "volume_divergence",
        report,
    )

    assert feedback["classification"] == "INCONCLUSIVE"
    assert "HIGH_REDUNDANCY" in feedback["tags"]
    assert "WEAK_INCREMENTAL_EVIDENCE" in feedback["tags"]
    assert (
        feedback["suggested_action"]
        == "REFINE_OR_EXPLORE_NEW_HYPOTHESIS"
    )


def test_promising_incremental_candidate():
    report = {
        "factor_similarity": {
            "momentum_periods_4": {
                "n": 1000,
                "pearson": 0.2,
                "spearman": 0.3,
            }
        },
        "incremental": {
            "auc": 0.01,
            "logloss": 0.002,
            "brier": 0.001,
        },
        "stability": {
            "robust_positive": True,
        },
    }

    feedback = build_research_feedback(
        "new_candidate",
        report,
    )

    assert feedback["classification"] == "PROMISING"
    assert feedback["suggested_action"] == "VERIFY_ON_NEW_DATA"
    assert "HIGH_REDUNDANCY" not in feedback["tags"]
