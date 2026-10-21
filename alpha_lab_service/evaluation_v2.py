"""Mode-aware descriptive evaluation for trusted Factor Service v2 runs."""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_VALID_COVERAGE = 0.8
MIN_VALID_CROSS_SECTIONS = 2


def _labels(run, expected: pd.MultiIndex) -> pd.Series:
    frame = run.dataset.evaluator_labels.frame.copy()
    time_column = "open_time" if "open_time" in frame else "decision_time"
    if time_column not in frame or "label_value" not in frame:
        raise ValueError("trusted evaluator labels lack timestamp or label_value")
    if "symbol" not in frame:
        if len(run.research_request.symbols) != 1:
            raise ValueError("cross-sectional evaluator labels require symbols")
        frame["symbol"] = run.research_request.symbols[0]
    frame["open_time"] = pd.to_datetime(frame[time_column], utc=True, errors="raise")
    frame["label_value"] = pd.to_numeric(frame["label_value"], errors="raise")
    labels = frame.set_index(["open_time", "symbol"])["label_value"].astype(float)
    labels.index = labels.index.set_names(["open_time", "symbol"])
    if not labels.index.is_unique:
        raise ValueError("trusted evaluator labels contain duplicate output keys")
    return labels.reindex(expected)


def _clean_inputs(outputs: pd.Series, run, identity: str) -> tuple[pd.DataFrame, str]:
    expected = pd.MultiIndex.from_tuples(
        [(stamp, symbol) for stamp in run.evaluation_timestamps for symbol in run.research_request.symbols],
        names=["open_time", "symbol"],
    )
    if not isinstance(outputs, pd.Series) or not outputs.index.equals(expected):
        raise ValueError("factor output must cover the complete authorized evaluation schedule in order")
    labels = _labels(run, expected)
    numeric = pd.to_numeric(outputs, errors="raise").astype(float)
    raw = pd.DataFrame({"factor": numeric, "label": labels}, index=expected)
    raw = raw.replace([np.inf, -np.inf], np.nan)
    raw["open_time"] = raw.index.get_level_values("open_time")
    valid = raw.dropna(subset=["factor", "label"]).copy()
    return valid, str(run.research_request.mode)


def _correlation(group: pd.DataFrame) -> tuple[float | None, float | None]:
    if len(group) < 3 or group.factor.nunique() < 2 or group.label.nunique() < 2:
        return None, None
    return float(group.factor.corr(group.label)), float(group.factor.corr(group.label, method="spearman"))


def _periodic_ic(valid: pd.DataFrame, period: str) -> dict[str, dict[str, float | None]]:
    if valid.empty:
        return {}
    keys = valid.open_time.dt.strftime(period)
    result = {}
    for key, group in valid.groupby(keys, sort=True):
        ic, rank_ic = _correlation(group)
        result[str(key)] = {"ic": ic, "rank_ic": rank_ic}
    return result


def _coverage(total: int, valid: pd.DataFrame, n_timestamps: int) -> dict:
    n = len(valid)
    return {
        "minimum_required_coverage_rate": MIN_VALID_COVERAGE,
        "requested_rows": total,
        "valid_rows": n,
        "missing_rows": total - n,
        "missing_rate": (total - n) / total if total else 1.0,
        "coverage_rate": n / total if total else 0.0,
        "requested_timestamps": n_timestamps,
    }


def _time_series_metrics(valid: pd.DataFrame, total: int, n_timestamps: int) -> tuple[str, str | None, dict]:
    coverage = _coverage(total, valid, n_timestamps)
    if len(valid) < 3:
        return "NOT_EVALUABLE", "INSUFFICIENT_VALID_OBSERVATIONS", {"coverage": coverage}
    if coverage["coverage_rate"] < MIN_VALID_COVERAGE:
        return "NOT_EVALUABLE", "INSUFFICIENT_COVERAGE", {"coverage": coverage}
    ic, rank_ic = _correlation(valid)
    if ic is None:
        return "NOT_EVALUABLE", "CONSTANT_FACTOR_OR_LABEL", {"coverage": coverage}
    monthly = _periodic_ic(valid, "%Y-%m")
    yearly = _periodic_ic(valid, "%Y")
    ranked = valid.copy()
    ranked["quantile"] = pd.qcut(ranked.factor.rank(method="first"), q=min(5, len(ranked)), labels=False)
    grouped = ranked.groupby("quantile", sort=True).label.agg(["mean", "count"])
    grouped.index = grouped.index.astype(int)
    grouped_returns = {str(key): {"mean_forward_return": float(row["mean"]), "observations": int(row["count"])}
                       for key, row in grouped.iterrows()}
    exposure = ranked.factor.rank(pct=True) * 2.0 - 1.0
    turnover = float(exposure.diff().abs().dropna().mean()) if len(exposure) > 1 else None
    cost_bps = 10.0
    cost_return = (2 * cost_bps * turnover / 10000.0) if turnover is not None else None
    return "EXPLORATORY", None, {
        "ic": ic, "rank_ic": rank_ic,
        "grouped_forward_returns": grouped_returns,
        "monthly": monthly, "yearly": yearly,
        "coverage": coverage,
        "turnover": {"mean_absolute_exposure_change": turnover},
        "cost_diagnostic": {
            "assumption_only": True, "cost_bps_per_side": cost_bps,
            "turnover": turnover, "estimated_cost_return": cost_return,
            "interpretation": "Assumption-based diagnostic; not executable PnL.",
        },
    }


def _cross_sectional_metrics(valid: pd.DataFrame, total: int, n_timestamps: int) -> tuple[str, str | None, dict]:
    coverage = _coverage(total, valid, n_timestamps)
    if coverage["coverage_rate"] < MIN_VALID_COVERAGE:
        return "NOT_EVALUABLE", "INSUFFICIENT_COVERAGE", {"coverage": coverage}
    per_time = []
    quantile_rows = []
    previous: tuple[set[str], set[str]] | None = None
    turnovers = []
    spreads = []
    for stamp, group in valid.groupby(level="open_time", sort=True):
        if len(group) < 5:
            continue
        ic, rank_ic = _correlation(group)
        if ic is None:
            continue
        symbols = group.index.get_level_values("symbol")
        ordered = group.assign(symbol=symbols).sort_values("factor", kind="mergesort")
        q = min(5, len(ordered))
        ranks = ordered.factor.rank(method="first")
        ordered["quantile"] = pd.qcut(ranks, q=q, labels=False).astype(int)
        for quantile, bucket in ordered.groupby("quantile", sort=True):
            quantile_rows.append({"open_time": stamp.isoformat(), "quantile": int(quantile),
                                  "mean_forward_return": float(bucket.label.mean()), "asset_count": len(bucket)})
        width = max(1, len(ordered) // 5)
        longs = set(ordered.symbol.iloc[-width:])
        shorts = set(ordered.symbol.iloc[:width])
        spreads.append(float(ordered.label.iloc[-width:].mean() - ordered.label.iloc[:width].mean()))
        if previous is not None:
            turnovers.append((len(longs ^ previous[0]) + len(shorts ^ previous[1])) / (4 * width))
        previous = longs, shorts
        per_time.append({"open_time": stamp.isoformat(), "ic": ic, "rank_ic": rank_ic,
                         "asset_count": int(len(group))})
    if not per_time:
        return "NOT_EVALUABLE", "NO_VALID_CROSS_SECTIONS", {"coverage": coverage, "asset_count": {"min": 0, "median": 0, "max": 0}}
    if len(per_time) < MIN_VALID_CROSS_SECTIONS:
        return "NOT_EVALUABLE", "INSUFFICIENT_VALID_CROSS_SECTIONS", {
            "coverage": coverage, "effective_cross_sections": len(per_time),
        }
    monthly = {}
    yearly = {}
    section_frame = pd.DataFrame(per_time)
    section_frame["open_time"] = pd.to_datetime(section_frame.open_time, utc=True)
    for period, target in (("%Y-%m", monthly), ("%Y", yearly)):
        target.update({str(key): float(value) for key, value in
                       section_frame.assign(period=section_frame.open_time.dt.strftime(period)).groupby("period").rank_ic.mean().items()})
    turnover = float(np.mean(turnovers)) if turnovers else None
    cost_bps = 10.0
    cost_return = (2 * cost_bps * turnover / 10000.0) if turnover is not None else None
    counts = section_frame.asset_count
    return "EXPLORATORY", None, {
        "mean_cs_ic": float(section_frame.ic.mean()),
        "mean_cs_rank_ic": float(section_frame.rank_ic.mean()),
        "effective_cross_sections": len(per_time), "per_time": per_time,
        "quantile_returns": quantile_rows,
        "asset_count": {"min": int(counts.min()), "median": float(counts.median()), "max": int(counts.max())},
        "monthly": monthly, "yearly": yearly, "coverage": coverage,
        "turnover": {"long_short_membership_turnover": turnover},
        "cost_diagnostic": {
            "assumption_only": True, "cost_bps_per_side": cost_bps,
            "mean_gross_long_short_return": float(np.mean(spreads)), "turnover": turnover,
            "estimated_cost_return": cost_return,
            "interpretation": "Assumption-based diagnostic; not executable PnL.",
        },
    }


def evaluate_authorized_factor_output(outputs: pd.Series, run, *, identity: str) -> dict:
    """Evaluate a complete trusted output projection without formal inference."""
    valid, mode = _clean_inputs(outputs, run, identity)
    total = len(outputs)
    n_timestamps = len(run.evaluation_timestamps)
    if mode == "time_series":
        if len(run.research_request.symbols) != 1:
            raise ValueError("time-series evaluation requires exactly one authorized symbol")
        status, reason, metrics = _time_series_metrics(valid, total, n_timestamps)
    elif mode == "cross_sectional":
        status, reason, metrics = _cross_sectional_metrics(valid, total, n_timestamps)
    else:
        raise ValueError("unsupported authorized research mode")
    return {
        "identity": identity, "mode": mode, "status": status, "reason": reason,
        "metrics": metrics, "formal_inference_status": "BLOCKED",
    }
