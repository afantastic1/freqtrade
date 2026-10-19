"""Cross-sectional metrics whose independent observations are UTC sections."""

from dataclasses import dataclass
from math import erf, sqrt

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CrossSectionalEvaluator:
    min_assets: int = 5
    n_boot: int = 1000
    block_size: int = 1
    random_state: int = 42
    quantile_fraction: float = 0.2
    cost_bps: float = 10.0
    min_sections: int = 8

    def evaluate(self, factor: pd.Series, label: pd.Series) -> dict:
        if self.n_boot < 2 or self.block_size < 1 or self.min_assets < 3 or self.min_sections < 2:
            raise ValueError("invalid cross-sectional inference configuration")
        if not 0 < self.quantile_fraction <= 0.5 or self.cost_bps < 0:
            raise ValueError("invalid quantile or cost configuration")
        data = pd.concat({"factor": factor, "label": label}, axis=1).replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        rows = []
        spreads = []
        prev_long = prev_short = None
        all_times = factor.index.get_level_values("open_time").unique()
        requested = factor.groupby(level="open_time").size()
        for timestamp, group in data.groupby(level="open_time", sort=True):
            if len(group) < self.min_assets:
                continue
            if group.factor.nunique() < 2 or group.label.nunique() < 2:
                continue
            rows.append({
                "open_time": timestamp,
                "n_assets": len(group),
                "cs_ic": group.factor.corr(group.label, method="pearson"),
                "cs_rank_ic": group.factor.corr(group.label, method="spearman"),
            })
            ordered = group.sort_values("factor")
            width = max(1, int(np.floor(len(group) * self.quantile_fraction)))
            short = set(ordered.index.get_level_values("symbol")[:width])
            long = set(ordered.index.get_level_values("symbol")[-width:])
            gross = float(ordered.label.iloc[-width:].mean() - ordered.label.iloc[:width].mean())
            turnover = None if prev_long is None else (
                len(long.symmetric_difference(prev_long)) +
                len(short.symmetric_difference(prev_short))
            ) / (4 * width)
            spreads.append({"open_time": timestamp, "gross_spread": gross,
                            "rank_turnover": turnover})
            prev_long, prev_short = long, short
        sections = pd.DataFrame(rows)
        if sections.empty:
            return {
                "status": "NOT_EVALUABLE", "reason": "NO_VALID_CROSS_SECTIONS",
                "effective_cross_sections": 0, "mean_cs_ic": None,
                "mean_cs_rank_ic": None, "asset_count": {"min": 0, "median": 0, "max": 0},
                "bootstrap": {"unit": "open_time", "valid_replicates": 0},
                "coverage": {"available_sections": len(all_times), "evaluated_sections": 0},
            }
        values = sections.cs_rank_ic.to_numpy(float)
        rng = np.random.default_rng(self.random_state)
        n = len(values)
        block = min(max(1, self.block_size), n)
        draws = np.empty(self.n_boot)
        for i in range(self.n_boot):
            starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
            idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
            draws[i] = values[idx].mean()
        low, high = np.quantile(draws, [0.025, 0.975])
        mean = float(values.mean())
        sd = float(values.std(ddof=1)) if n > 1 else float("nan")
        null = draws - mean
        p = float(((np.abs(null) >= abs(mean)).sum() + 1) / (self.n_boot + 1))
        # Bartlett-kernel HAC on time-section means. Asset count never enters n.
        centered = values - mean
        lag = min(max(self.block_size - 1, 0), n - 1)
        long_run_var = float(np.dot(centered, centered) / n)
        for offset in range(1, lag + 1):
            gamma = float(np.dot(centered[offset:], centered[:-offset]) / n)
            long_run_var += 2 * (1 - offset / (lag + 1)) * gamma
        hac_se = float(np.sqrt(max(long_run_var, 0.0) / n))
        spread_frame = pd.DataFrame(spreads)
        mean_turnover = float(spread_frame.rank_turnover.dropna().mean()) if spread_frame.rank_turnover.notna().any() else 0.0
        gross = float(spread_frame.gross_spread.mean())
        cost = self.cost_bps / 10000.0 * 2 * mean_turnover
        boot_se = float(draws.std(ddof=1))
        monthly = sections.assign(period=sections.open_time.dt.strftime("%Y-%m")).groupby("period").cs_rank_ic.mean()
        yearly = sections.assign(period=sections.open_time.dt.strftime("%Y")).groupby("period").cs_rank_ic.mean()

        def _normal_cdf(value):
            return 0.5 * (1 + erf(value / sqrt(2)))

        effect = 0.02
        normal_power = (1 - _normal_cdf(1.9599639845 - effect / boot_se) +
                        _normal_cdf(-1.9599639845 - effect / boot_se)) if boot_se > 0 else None
        return {
            "status": "EVALUATED" if n >= self.min_sections else "INCONCLUSIVE",
            "reason": None if n >= self.min_sections else "TOO_FEW_TIME_SECTIONS",
            "effective_cross_sections": n,
            "mean_cs_ic": float(sections.cs_ic.mean()),
            "mean_cs_rank_ic": mean,
            "cs_icir": float(mean / sd) if sd > 0 else None,
            "asset_count": {
                "min": int(sections.n_assets.min()),
                "median": float(sections.n_assets.median()),
                "max": int(sections.n_assets.max()),
            },
            "per_time": sections.to_dict("records"),
            "coverage": {
                "available_sections": len(all_times), "evaluated_sections": n,
                "section_ratio": n / len(all_times),
                "mean_valid_asset_ratio": float((sections.set_index("open_time").n_assets / requested.reindex(sections.open_time).to_numpy()).mean()),
            },
            "hac": {"kernel": "Bartlett", "lag": lag,
                    "standard_error": hac_se,
                    "t_statistic": mean / hac_se if hac_se > 0 else None},
            "top_bottom": {"quantile_fraction": self.quantile_fraction,
                           "mean_gross_spread": gross, "mean_rank_turnover": mean_turnover,
                           "cost_bps_per_side": self.cost_bps,
                           "mean_net_spread": gross - cost,
                           "per_time": spreads},
            "direction_stability": {
                "monthly": {str(key): float(value) for key, value in monthly.items()},
                "yearly": {str(key): float(value) for key, value in yearly.items()},
                "positive_months": int((monthly > 0).sum()),
                "valid_months": len(monthly),
                "positive_years": int((yearly > 0).sum()),
                "valid_years": len(yearly),
            },
            "mde": {"rank_ic": 2.8015852 * boot_se if boot_se > 0 else None,
                    "unit": "cross_section_mean", "assumption": "moving block bootstrap SE",
                    "power_at_rank_ic_0_02": normal_power,
                    "power_assumption": "normal approximation, two sided alpha=0.05, effect=0.02"},
            "bootstrap": {
                "unit": "open_time", "block_size": block,
                "valid_replicates": self.n_boot,
                "ci_95_lower": float(low), "ci_95_upper": float(high),
                "p_two_sided": p, "null": "centered moving block distribution",
            },
        }
