"""Prespecified synthetic calibration for cross-sectional RankIC inference.

This module is an audit tool. It does not import or change the production
evaluator and never reads market data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import rankdata, t


METHODS = tuple(
    [{"name": f"mbb_lag_{lag}", "family": "moving_block", "parameter": lag}
     for lag in (4, 12, 32)]
    + [{"name": f"hac_lag_{lag}", "family": "hac", "parameter": lag}
       for lag in (4, 12, 32)]
    + [{"name": "iid_t", "family": "iid_t", "parameter": 0}]
)


def _forward_window_means(returns: np.ndarray, sections: int, horizon: int) -> np.ndarray:
    """Return exactly ``horizon`` future rows for each decision section."""
    if horizon < 1 or returns.shape[0] < sections + horizon:
        raise ValueError("returns do not cover every complete forward window")
    windows = np.lib.stride_tricks.sliding_window_view(
        returns, window_shape=horizon, axis=0
    )
    return windows[1:sections + 1].mean(axis=-1)


def calibration_matrix() -> list[dict]:
    """Return the scenario grid before any calibration outcomes are observed."""
    matrix = []
    for rho in (0.0, 0.5, 0.9):
        for assets in (5, 10, 25):
            for sections in (80, 250):
                matrix.append({"rho": rho, "common_shock": 0.0,
                               "heteroskedastic": False, "assets": assets,
                               "sections": sections, "horizon": 1,
                               "effect": 0.0, "kind": "null_dependence"})
    for rho in (0.0, 0.5, 0.9):
        for horizon in (1, 4, 12):
            matrix.append({"rho": rho, "common_shock": 0.5,
                           "heteroskedastic": True, "assets": 10,
                           "sections": 250, "horizon": horizon,
                           "effect": 0.0, "kind": "null_joint_stress"})
    matrix.extend([
        {"rho": 0.9, "common_shock": 0.5, "heteroskedastic": False,
         "assets": 10, "sections": 250, "horizon": 4, "effect": 0.0,
         "kind": "null_common_shock_only"},
        {"rho": 0.9, "common_shock": 0.0, "heteroskedastic": True,
         "assets": 10, "sections": 250, "horizon": 4, "effect": 0.0,
         "kind": "null_heteroskedastic_only"},
    ])
    for rho in (0.0, 0.5, 0.9):
        for sections in (80, 250):
            matrix.append({"rho": rho, "common_shock": 0.0,
                           "heteroskedastic": False, "assets": 10,
                           "sections": sections, "horizon": 4,
                           "effect": 0.105, "kind": "power_and_coverage"})
    return matrix


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    if trials <= 0 or not 0 <= successes <= trials or not 0 < confidence < 1:
        raise ValueError("invalid binomial interval inputs")
    z = float(t.ppf(0.5 + confidence / 2, df=1e12))
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    radius = z * np.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return float(max(0, center - radius)), float(min(1, center + radius))


def simulate_rank_ic(*, seed: int, assets: int, sections: int, rho: float,
                     common_shock: float, heteroskedastic: bool,
                     horizon: int, effect: float) -> np.ndarray:
    """Simulate UTC-section Spearman ICs under a Gaussian copula panel DGP."""
    if assets < 3 or sections < 2 or not -0.99 < rho < 0.99:
        raise ValueError("invalid simulation dimensions or serial correlation")
    if not 0 <= common_shock < 1 or horizon < 1 or effect < 0:
        raise ValueError("invalid simulation parameters")
    rng = np.random.default_rng(seed)
    factor_innovations = rng.normal(size=(sections, assets))
    factor = np.empty_like(factor_innovations)
    factor[0] = factor_innovations[0]
    for index in range(1, sections):
        factor[index] = rho * factor[index - 1] + np.sqrt(1 - rho * rho) * factor_innovations[index]
    length = sections + horizon
    innovations = rng.normal(size=(length, assets))
    idio = np.empty_like(innovations)
    idio[0] = innovations[0]
    scale = np.sqrt(1 - rho * rho)
    for index in range(1, len(innovations)):
        idio[index] = rho * idio[index - 1] + scale * innovations[index]
    market_innovations = rng.normal(size=length)
    market = np.empty_like(market_innovations)
    market[0] = market_innovations[0]
    for index in range(1, len(market)):
        market[index] = rho * market[index - 1] + scale * market_innovations[index]
    loadings = rng.normal(size=assets)
    idio_scale = np.exp(rng.normal(0, 0.25, size=assets))
    if heteroskedastic:
        variance = np.empty_like(idio)
        variance[0] = 1.0
        for index in range(1, len(idio)):
            variance[index] = (0.05 + 0.90 * variance[index - 1]
                               + 0.05 * innovations[index - 1] ** 2)
        idio_scale = idio_scale * np.sqrt(variance)
    returns = np.sqrt(1 - common_shock) * idio * idio_scale
    returns += np.sqrt(common_shock) * market[:, None] * loadings[None, :]
    # A fixed, declarative alternative adds a rank-predictive component to the
    # forward target. effect=0 is the exact zero-effect null.
    labels = _forward_window_means(returns, sections, horizon) + effect * factor
    factor_ranks = rankdata(factor, axis=1)
    label_ranks = rankdata(labels, axis=1)
    factor_ranks -= factor_ranks.mean(axis=1, keepdims=True)
    label_ranks -= label_ranks.mean(axis=1, keepdims=True)
    numerator = (factor_ranks * label_ranks).sum(axis=1)
    denominator = np.sqrt((factor_ranks**2).sum(axis=1) * (label_ranks**2).sum(axis=1))
    return numerator / denominator


def _simulate_rank_ic_batch(rng: np.random.Generator, *, repetitions: int,
                            assets: int, sections: int, rho: float,
                            common_shock: float, heteroskedastic: bool,
                            horizon: int, effect: float) -> np.ndarray:
    """Vectorized equivalent of ``simulate_rank_ic`` for Monte Carlo runs."""
    length = sections + horizon
    factor_innovations = rng.normal(size=(repetitions, sections, assets))
    factor = np.empty_like(factor_innovations)
    factor[:, 0] = factor_innovations[:, 0]
    scale = np.sqrt(1 - rho * rho)
    for index in range(1, sections):
        factor[:, index] = rho * factor[:, index - 1] + scale * factor_innovations[:, index]
    innovations = rng.normal(size=(repetitions, length, assets))
    idio = np.empty_like(innovations)
    idio[:, 0] = innovations[:, 0]
    for index in range(1, length):
        idio[:, index] = rho * idio[:, index - 1] + scale * innovations[:, index]
    market_innovations = rng.normal(size=(repetitions, length))
    market = np.empty_like(market_innovations)
    market[:, 0] = market_innovations[:, 0]
    for index in range(1, length):
        market[:, index] = rho * market[:, index - 1] + scale * market_innovations[:, index]
    loadings = rng.normal(size=(repetitions, assets))
    idio_scale = np.exp(rng.normal(0, 0.25, size=(repetitions, assets)))
    if heteroskedastic:
        variance = np.empty_like(innovations)
        variance[:, 0] = 1.0
        for index in range(1, length):
            variance[:, index] = (0.05 + 0.90 * variance[:, index - 1]
                                  + 0.05 * innovations[:, index - 1] ** 2)
        idio_scale = idio_scale[:, None, :] * np.sqrt(variance)
    else:
        idio_scale = idio_scale[:, None, :]
    returns = np.sqrt(1 - common_shock) * idio * idio_scale
    returns += np.sqrt(common_shock) * market[:, :, None] * loadings[:, None, :]
    windows = np.lib.stride_tricks.sliding_window_view(returns, window_shape=horizon, axis=1)
    labels = windows[:, 1:sections + 1].mean(axis=-1) + effect * factor
    factor_ranks = rankdata(factor, axis=2)
    label_ranks = rankdata(labels, axis=2)
    factor_ranks -= factor_ranks.mean(axis=2, keepdims=True)
    label_ranks -= label_ranks.mean(axis=2, keepdims=True)
    numerator = (factor_ranks * label_ranks).sum(axis=2)
    denominator = np.sqrt((factor_ranks**2).sum(axis=2) * (label_ranks**2).sum(axis=2))
    return numerator / denominator


def _moving_block(values: np.ndarray, block_size: int, *, n_boot: int,
                  seed: int) -> tuple[float, float, float]:
    n = len(values)
    block = min(block_size, n)
    blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1, size=(n_boot, blocks))
    offsets = np.arange(block)
    samples = values[starts[..., None] + offsets].reshape(n_boot, -1)[:, :n]
    means = samples.mean(axis=1)
    point = float(values.mean())
    low, high = np.quantile(means, [0.025, 0.975])
    null = means - point
    pvalue = (np.count_nonzero(np.abs(null) >= abs(point)) + 1) / (n_boot + 1)
    return float(low), float(high), float(pvalue)


def _hac(values: np.ndarray, lag: int) -> tuple[float, float, float]:
    n = len(values)
    mean = float(values.mean())
    centered = values - mean
    lag = min(lag, n - 1)
    long_run = float(centered @ centered / n)
    for offset in range(1, lag + 1):
        gamma = float(centered[offset:] @ centered[:-offset] / n)
        long_run += 2 * (1 - offset / (lag + 1)) * gamma
    se = np.sqrt(max(0.0, long_run) / n)
    df = max(1, n - lag - 1)
    critical = float(t.ppf(0.975, df))
    if se == 0:
        return mean, mean, 1.0
    statistic = abs(mean / se)
    pvalue = float(2 * t.sf(statistic, df))
    return mean - critical * se, mean + critical * se, pvalue


def infer(values: np.ndarray, method: dict, *, n_boot: int, seed: int) -> tuple[float, float, float]:
    if method["family"] == "moving_block":
        return _moving_block(values, method["parameter"], n_boot=n_boot, seed=seed)
    if method["family"] == "hac":
        return _hac(values, method["parameter"])
    mean = float(values.mean())
    se = float(values.std(ddof=1) / np.sqrt(len(values)))
    df = len(values) - 1
    if se == 0:
        return mean, mean, 1.0
    critical = float(t.ppf(0.975, df))
    pvalue = float(2 * t.sf(abs(mean / se), df))
    return mean - critical * se, mean + critical * se, pvalue


def _infer_batch(values: np.ndarray, method: dict, *, n_boot: int,
                 rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized intervals and tests for rows of section-level RankIC."""
    repetitions, count = values.shape
    means = values.mean(axis=1)
    if method["family"] == "moving_block":
        block = min(method["parameter"], count)
        n_blocks = int(np.ceil(count / block))
        starts = rng.integers(
            0, count - block + 1, size=(repetitions, n_boot, n_blocks)
        )
        indices = (starts[..., None] + np.arange(block)).reshape(
            repetitions, n_boot, -1
        )[:, :, :count]
        samples = np.take_along_axis(values[:, None, :], indices, axis=2).mean(axis=2)
        low, high = np.quantile(samples, [0.025, 0.975], axis=1)
        null = samples - means[:, None]
        pvalues = (np.count_nonzero(np.abs(null) >= np.abs(means[:, None]), axis=1) + 1) / (n_boot + 1)
        return low, high, pvalues
    centered = values - means[:, None]
    if method["family"] == "hac":
        lag = min(method["parameter"], count - 1)
        long_run = (centered * centered).sum(axis=1) / count
        for offset in range(1, lag + 1):
            gamma = (centered[:, offset:] * centered[:, :-offset]).sum(axis=1) / count
            long_run += 2 * (1 - offset / (lag + 1)) * gamma
        se = np.sqrt(np.maximum(long_run, 0.0) / count)
        df = max(1, count - lag - 1)
    else:
        se = values.std(axis=1, ddof=1) / np.sqrt(count)
        df = count - 1
    critical = float(t.ppf(0.975, df))
    statistic = np.divide(np.abs(means), se, out=np.full_like(means, np.inf), where=se > 0)
    pvalues = 2 * t.sf(statistic, df)
    pvalues = np.where(se > 0, pvalues, 1.0)
    return means - critical * se, means + critical * se, pvalues


def run_calibration(*, repetitions: int = 5000, n_boot: int = 399,
                    seed: int = 20261019, matrix: list[dict] | None = None) -> dict:
    if repetitions < 100 or n_boot < 99:
        raise ValueError("calibration requires repetitions >= 100 and n_boot >= 99")
    scenarios = matrix if matrix is not None else calibration_matrix()
    output = []
    covered_count = len(scenarios) * len(METHODS)
    familywise_confidence = 1 - 0.05 / covered_count
    for scenario_index, scenario in enumerate(scenarios):
        reference_mean = None
        if scenario["effect"] > 0:
            reference = simulate_rank_ic(
                seed=seed + scenario_index * repetitions + repetitions + 1,
                assets=scenario["assets"], sections=50000, rho=scenario["rho"],
                common_shock=scenario["common_shock"],
                heteroskedastic=scenario["heteroskedastic"],
                horizon=scenario["horizon"], effect=scenario["effect"],
            )
            reference_mean = float(reference.mean())
        records = {method["name"]: {"reject": 0, "cover_null": 0,
                                      "cover_alternative": 0}
                   for method in METHODS}
        rng = np.random.default_rng(seed + scenario_index * repetitions)
        chunk_size = 64
        for first in range(0, repetitions, chunk_size):
            count = min(chunk_size, repetitions - first)
            series_batch = _simulate_rank_ic_batch(
                rng, repetitions=count, assets=scenario["assets"],
                sections=scenario["sections"], rho=scenario["rho"],
                common_shock=scenario["common_shock"],
                heteroskedastic=scenario["heteroskedastic"],
                horizon=scenario["horizon"], effect=scenario["effect"],
            )
            for method in METHODS:
                low, high, pvalue = _infer_batch(
                    series_batch, method, n_boot=n_boot,
                    rng=np.random.default_rng(seed + scenario_index * repetitions + first + method["parameter"]),
                )
                record = records[method["name"]]
                record["reject"] += int((pvalue < 0.05).sum())
                record["cover_null"] += int(((low <= 0) & (high >= 0)).sum())
                if scenario["effect"] > 0:
                    record["cover_alternative"] += int(((low <= reference_mean) & (high >= reference_mean)).sum())
        method_results = {}
        for method in METHODS:
            record = records[method["name"]]
            rejects = record["reject"]
            covers = record["cover_null"]
            method_results[method["name"]] = {
                "rejection_rate": rejects / repetitions,
                "rejection_rate_wilson_95": wilson_interval(rejects, repetitions),
                "rejection_rate_wilson_familywise_95": wilson_interval(
                    rejects, repetitions, confidence=familywise_confidence),
                "null_interval_coverage": covers / repetitions,
                "null_coverage_wilson_95": wilson_interval(covers, repetitions),
                "null_coverage_wilson_familywise_95": wilson_interval(
                    covers, repetitions, confidence=familywise_confidence),
                "alternative_interval_coverage": (
                    record["cover_alternative"] / repetitions
                    if scenario["effect"] > 0 else None
                ),
                "alternative_coverage_wilson_familywise_95": (
                    wilson_interval(record["cover_alternative"], repetitions,
                                    confidence=familywise_confidence)
                    if scenario["effect"] > 0 else None
                ),
                "power": rejects / repetitions if scenario["effect"] > 0 else None,
                "power_wilson_95": (
                    wilson_interval(rejects, repetitions)
                    if scenario["effect"] > 0 else None
                ),
            }
        output.append({**scenario, "population_reference_mean_cs_rank_ic": reference_mean,
                       "methods": method_results})
    return {
        "calibration_version": "cs-inference-mc-prespecified-v1",
        "seed": seed, "repetitions": repetitions, "bootstrap_replicates": n_boot,
        "alpha": 0.05, "nominal_interval": 0.95,
        "familywise_confidence": familywise_confidence,
        "acceptance_criteria": {
            "type_i": "Across all null scenarios and methods, use Bonferroni simultaneous 95% Wilson intervals. A method passes only if every simultaneous upper bound is <= 0.06.",
            "coverage": "Use Bonferroni simultaneous 95% Wilson intervals across every scenario and method. Null and alternative interval coverage must stay within 0.92-0.98 in every applicable scenario.",
            "power": "Report power and its ordinary 95% Wilson interval for all prespecified alternatives. Power cannot override a Type-I or coverage failure.",
        },
        "methods": list(METHODS), "scenario_count": len(output), "scenarios": output,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5000)
    parser.add_argument("--bootstrap-replicates", type=int, default=399)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_calibration(repetitions=args.repetitions,
                             n_boot=args.bootstrap_replicates, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"calibration_version": result["calibration_version"],
                      "scenario_count": result["scenario_count"],
                      "repetitions": result["repetitions"],
                      "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
