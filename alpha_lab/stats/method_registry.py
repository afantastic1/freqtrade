"""Versioned status registry for methods allowed at formal decision gates."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType

REGISTRY_VERSION = "alpha-stat-method-registry-v1"

METHOD_REGISTRY = {
    "paired-mbb-centered-null-v3": {
        "status": "LEGACY",
        "role": "inference",
        "scope": "time-series paired model metrics",
        "reason": "Historical method; current evidence does not establish calibrated formal p-values.",
    },
    "prereg-a-v3": {
        "status": "LEGACY",
        "role": "inference",
        "scope": "time-series regime and incremental evaluation",
        "reason": "Uses legacy paired moving-block inference; reproduce history only.",
    },
    "prereg-b-v2-centered-null": {
        "status": "LEGACY",
        "role": "inference",
        "scope": "non-OHLCV time-series RankIC",
        "reason": "Uses custom moving-block inference without accepted calibration evidence.",
    },
    "cs-time-block-centered-v1": {
        "status": "BLOCKED",
        "role": "inference",
        "scope": "cross-sectional section-level RankIC",
        "reason": "Recorded calibration failed the prespecified Type-I and coverage requirements.",
    },
    "hac-bartlett-calibration-v1": {
        "status": "BLOCKED",
        "role": "inference",
        "scope": "tested section-level Bartlett HAC configurations",
        "reason": "Recorded calibration failed; no lag selection is approved for formal use.",
    },
    "estimated_phi": {
        "status": "BLOCKED",
        "role": "inference",
        "scope": "historical AR(1) calibration claim supplied with task",
        "reason": "Reported Type-I error is 16.20%; implementation and source artifact are absent from this checkout.",
    },
    "ts-rankic-mbb-horizon-v1": {
        "status": "LEGACY",
        "role": "inference",
        "scope": "pooled time-series RankIC",
        "reason": "Custom row-block bootstrap; fixed horizon blocks do not establish dependence-robust formal inference.",
    },
    "n-eff-design-horizon-v1": {
        "status": "LEGACY",
        "role": "diagnostic",
        "scope": "non-overlapping label-window count",
        "reason": "n/horizon is a design diagnostic, not a general effective sample-size estimator.",
    },
    "newey-west-vif-v1": {
        "status": "LEGACY",
        "role": "diagnostic",
        "scope": "autocorrelation-based sample-size diagnostic",
        "reason": "Handwritten, truncated-lag VIF is diagnostic only and is not a robust standard error.",
    },
    "statsmodels-newey-west": {
        "status": "UNSUPPORTED",
        "role": "inference",
        "scope": "Newey-West HAC",
        "reason": "Library is not a project dependency and no project adapter is validated.",
    },
    "linearmodels-fama-macbeth": {
        "status": "UNSUPPORTED",
        "role": "inference",
        "scope": "cross-sectional regression coefficient returns",
        "reason": "No project implementation or panel contract exists; not a RankIC substitute.",
    },
    "white-reality-check": {
        "status": "UNSUPPORTED",
        "role": "inference",
        "scope": "multiple strategy search",
        "reason": "No project implementation or declared strategy family exists.",
    },
    "hansen-spa": {
        "status": "UNSUPPORTED",
        "role": "inference",
        "scope": "multiple strategy search",
        "reason": "No project implementation or declared strategy family exists.",
    },
    "scipy-bh-v1": {
        "status": "PASS",
        "role": "adjustment_only",
        "scope": "p-value adjustment under independence/positive dependence",
        "reason": "Delegates to scipy.stats.false_discovery_control(method='bh'); requires valid marginal p-values and a declared family.",
    },
    "scipy-pearson-point-v1": {
        "status": "PASS",
        "role": "descriptive",
        "scope": "Pearson correlation point estimate",
        "reason": "Uses SciPy coefficient only; no IID p-value is promoted to time-series inference.",
    },
    "scipy-spearman-point-v1": {
        "status": "PASS",
        "role": "descriptive",
        "scope": "Spearman correlation point estimate",
        "reason": "Uses SciPy coefficient only; no IID p-value is promoted to time-series inference.",
    },
    "scipy-by-v1": {
        "status": "PASS",
        "role": "adjustment_only",
        "scope": "p-value adjustment under arbitrary dependence",
        "reason": "Delegates to scipy.stats.false_discovery_control(method='by'); requires valid marginal p-values and a declared family.",
    },
    "holm-bonferroni-v1": {
        "status": "PASS",
        "role": "adjustment_only",
        "scope": "family-wise error adjustment",
        "reason": "Standard Holm step-down adjustment; requires valid marginal p-values and a declared family.",
    },
}
METHOD_REGISTRY = MappingProxyType({
    method_id: MappingProxyType(entry)
    for method_id, entry in METHOD_REGISTRY.items()
})


def method_entry(method_id: str) -> dict | None:
    """Return a detached registry entry; unknown identifiers fail closed."""
    entry = METHOD_REGISTRY.get(method_id)
    return deepcopy(dict(entry)) if entry is not None else None
