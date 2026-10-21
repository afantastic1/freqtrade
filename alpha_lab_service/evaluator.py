"""Trusted descriptive projection over the frozen cross-sectional evaluator."""

from __future__ import annotations

import numpy as np
import pandas as pd

from alpha_lab.evaluation.cross_sectional_evaluator import CrossSectionalEvaluator


def evaluate_factor_output(outputs: pd.Series, labels: pd.Series, *, baseline: pd.Series | None,
                           identity: str) -> dict:
    if not isinstance(outputs, pd.Series) or not isinstance(labels, pd.Series):
        raise TypeError("factor output and labels must be Series")
    if not outputs.index.is_unique or not labels.index.is_unique:
        raise ValueError("factor and label indexes must be unique")
    if not outputs.index.equals(labels.index):
        raise ValueError("factor and label indexes must align exactly")
    if baseline is not None and (not baseline.index.is_unique or not baseline.index.equals(outputs.index)):
        raise ValueError("baseline index must align exactly")
    if not isinstance(outputs.index, pd.MultiIndex) or list(outputs.index.names) != ["open_time", "symbol"]:
        raise ValueError("factor output requires an (open_time, symbol) index")
    numeric = outputs.to_numpy(dtype=float)
    if np.isinf(numeric).any():
        raise ValueError("factor output must not contain infinity")

    # Reuse the frozen evaluator for point estimates, then expose only a clearly
    # descriptive projection. Inference outputs remain inaccessible at this API.
    evaluator = CrossSectionalEvaluator(min_assets=5, n_boot=2, min_sections=2)
    report = evaluator.evaluate(outputs, labels)
    metrics = {
        "mean_cs_ic": report["mean_cs_ic"],
        "mean_cs_rank_ic": report["mean_cs_rank_ic"],
        "effective_cross_sections": report["effective_cross_sections"],
        "coverage": {
            **report.get("coverage", {}),
            "valid_rows": int(pd.concat([outputs, labels], axis=1).replace([np.inf, -np.inf], np.nan).dropna().shape[0]),
        },
        "stability": report.get("direction_stability", {"monthly": {}, "yearly": {}}),
    }
    if baseline is not None:
        base_report = evaluator.evaluate(baseline, labels)
        base_rank_ic = base_report.get("mean_cs_rank_ic")
        candidate_rank_ic = report.get("mean_cs_rank_ic")
        metrics["incremental"] = {
            "baseline_mean_cs_rank_ic": base_rank_ic,
            "candidate_mean_cs_rank_ic": candidate_rank_ic,
            "descriptive_delta_mean_cs_rank_ic": (
                candidate_rank_ic - base_rank_ic
                if candidate_rank_ic is not None and base_rank_ic is not None else None
            ),
        }
    status = "EXPLORATORY" if report["effective_cross_sections"] >= 2 else "DIAGNOSTIC"
    return {"identity": identity, "status": status, "metrics": metrics}


def evaluate_authorized_factor_output(outputs: pd.Series, run, *, identity: str) -> dict:
    """Evaluate only a complete factor projection against labels from the trusted build."""
    from alpha_lab_service.evaluation_v2 import evaluate_authorized_factor_output as evaluate_v2

    return evaluate_v2(outputs, run, identity=identity)
