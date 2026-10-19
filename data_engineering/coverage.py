"""Preflight feature coverage before complete-case cross-sectional evaluation."""

from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel


def preflight_feature_coverage(
    panel: MarketPanel,
    features: Mapping[str, pd.Series],
    *,
    membership: pd.Series | None = None,
    min_assets_per_time: int,
    min_feature_coverage: float,
) -> dict:
    """Report rows that survive dataset-style complete-case filtering.

    Thresholds are required caller inputs; this diagnostic does not alter the
    frozen evaluator, select features, fill missing values, or infer statistics.
    """
    if not features:
        raise ValueError("at least one feature is required")
    if (isinstance(min_assets_per_time, bool)
            or not isinstance(min_assets_per_time, (int, np.integer))
            or min_assets_per_time < 1):
        raise ValueError("min_assets_per_time must be a positive integer")
    if not isinstance(min_feature_coverage, (int, float)) or not math.isfinite(
        min_feature_coverage
    ) or not 0 <= min_feature_coverage <= 1:
        raise ValueError("min_feature_coverage must be between zero and one")

    index = panel.frame.index
    if membership is None:
        eligible = pd.Series(True, index=index)
    else:
        if not membership.index.equals(index) or membership.isna().any():
            raise ValueError("membership must be non-null and aligned with panel")
        eligible = membership.astype(bool)
    for name, values in features.items():
        if not name or not isinstance(values, pd.Series) or not values.index.equals(index):
            raise ValueError("each feature must be a named Series aligned with panel")

    eligible_index = index[eligible.to_numpy()]
    feature_frame = pd.DataFrame(features, index=index).replace([np.inf, -np.inf], np.nan)
    eligible_features = feature_frame.loc[eligible]
    denominators = len(eligible_features)
    feature_coverage = {
        name: {
            "valid_rows": int(eligible_features[name].notna().sum()),
            "coverage": (
                float(eligible_features[name].notna().mean()) if denominators else 0.0
            ),
        }
        for name in features
    }
    complete = eligible_features.dropna(subset=list(features))
    times = pd.DatetimeIndex(eligible_index.get_level_values("open_time").unique()).sort_values()
    per_time = {}
    for time in times:
        mask = eligible_features.index.get_level_values("open_time") == time
        count = int(eligible_features.loc[mask].dropna(subset=list(features)).shape[0])
        per_time[time.isoformat()] = count
    minimum_observed = min(per_time.values(), default=0)
    feature_gates_pass = all(
        item["coverage"] >= min_feature_coverage for item in feature_coverage.values()
    )
    status = (
        "READY" if per_time and feature_gates_pass
        and minimum_observed >= min_assets_per_time else "NOT_EVALUABLE"
    )
    return {
        "status": status,
        "eligible_rows": denominators,
        "complete_case_rows": int(len(complete)),
        "feature_coverage": feature_coverage,
        "effective_assets_by_time": per_time,
        "minimum_effective_assets_observed": minimum_observed,
        "min_assets_per_time_required": min_assets_per_time,
        "min_feature_coverage_required": float(min_feature_coverage),
    }
