import numpy as np
import pandas as pd
import pytest

from alpha_lab.evaluation.cross_sectional_evaluator import CrossSectionalEvaluator


def panel_values(reverse=False):
    times = pd.date_range("2024-01-01", periods=12, freq="D", tz="UTC")
    symbols = ["A", "B", "C", "D", "E"]
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    factor = np.tile(np.arange(5.0), len(times))
    label = -factor if reverse else factor.copy()
    return pd.Series(factor, index=index), pd.Series(label, index=index)


def test_perfect_and_reverse_cs_rank_ic_golden():
    evaluator = CrossSectionalEvaluator(min_assets=3, n_boot=99, random_state=7)
    factor, label = panel_values()
    assert evaluator.evaluate(factor, label)["mean_cs_rank_ic"] == pytest.approx(1.0)
    factor, label = panel_values(reverse=True)
    assert evaluator.evaluate(factor, label)["mean_cs_rank_ic"] == pytest.approx(-1.0)


def test_degenerate_cross_sections_are_not_evaluable():
    factor, label = panel_values()
    factor[:] = 1.0
    result = CrossSectionalEvaluator(min_assets=3, n_boot=20).evaluate(factor, label)
    assert result["status"] == "NOT_EVALUABLE"
    assert result["effective_cross_sections"] == 0
    assert result["mean_cs_rank_ic"] is None


def test_time_block_bootstrap_treats_sections_as_observations():
    factor, label = panel_values()
    result = CrossSectionalEvaluator(min_assets=3, n_boot=49).evaluate(factor, label)
    assert result["effective_cross_sections"] == 12
    assert result["asset_count"]["min"] == 5
    assert result["bootstrap"]["unit"] == "open_time"


def test_shuffled_null_does_not_systematically_pass_significance():
    rng = np.random.default_rng(2026)
    times = pd.date_range("2024-01-01", periods=80, freq="h", tz="UTC")
    index = pd.MultiIndex.from_product([times, list("ABCDEFGH")], names=["open_time", "symbol"])
    factor = pd.Series(np.tile(np.arange(8, dtype=float), len(times)), index=index)
    false_positives = 0
    for seed in range(20):
        labels = pd.Series(rng.normal(size=len(index)), index=index)
        result = CrossSectionalEvaluator(min_assets=5, n_boot=199, block_size=4,
                                         random_state=seed).evaluate(factor, labels)
        false_positives += result["bootstrap"]["p_two_sided"] < 0.05
    assert false_positives <= 4


def test_evaluator_reports_coverage_hac_and_cost_spread():
    factor, label = panel_values()
    result = CrossSectionalEvaluator(min_assets=3, n_boot=99, random_state=7).evaluate(factor, label)
    assert result["coverage"]["evaluated_sections"] == 12
    assert result["hac"]["standard_error"] >= 0
    assert result["top_bottom"]["mean_gross_spread"] > 0
    assert result["top_bottom"]["mean_net_spread"] <= result["top_bottom"]["mean_gross_spread"]
