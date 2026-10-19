import numpy as np

from alpha_lab.calibration.cs_inference_calibration import (
    METHODS,
    _infer_batch,
    _forward_window_means,
    calibration_matrix,
    simulate_rank_ic,
    wilson_interval,
)


def test_prespecified_matrix_covers_requested_dependence_axes():
    matrix = calibration_matrix()
    assert {row["rho"] for row in matrix} == {0.0, 0.5, 0.9}
    assert {row["common_shock"] for row in matrix} >= {0.0, 0.5}
    assert {row["heteroskedastic"] for row in matrix} == {False, True}
    assert {row["assets"] for row in matrix} >= {5, 10, 25}
    assert {row["sections"] for row in matrix} >= {80, 250}
    assert {row["horizon"] for row in matrix} >= {1, 4, 12}
    assert {method["family"] for method in METHODS} >= {"moving_block", "hac", "iid_t"}


def test_rank_ic_simulation_is_deterministic_and_finite():
    options = dict(seed=123, assets=10, sections=80, rho=0.5,
                   common_shock=0.5, heteroskedastic=True, horizon=4, effect=0.0)
    first = simulate_rank_ic(**options)
    second = simulate_rank_ic(**options)
    assert first.shape == (80,)
    assert np.isfinite(first).all()
    np.testing.assert_array_equal(first, second)


def test_forward_windows_include_exact_horizon_even_at_last_section():
    returns = np.arange(12, dtype=float)[:, None]
    one_step = _forward_window_means(returns, sections=10, horizon=1)
    four_step = _forward_window_means(returns, sections=8, horizon=4)
    np.testing.assert_array_equal(one_step[:, 0], np.arange(1, 11))
    np.testing.assert_array_equal(four_step[:, 0], np.arange(2, 10) + 0.5)


def test_wilson_interval_is_bounded_and_contains_observed_rate():
    low, high = wilson_interval(50, 1000, confidence=0.95)
    assert 0 <= low <= 0.05 <= high <= 1


def test_bootstrap_randomization_is_independent_between_simulations():
    values = np.tile(np.random.default_rng(123).normal(size=80), (24, 1))
    method = {"family": "moving_block", "parameter": 8}
    _, _, pvalues = _infer_batch(values, method, n_boot=199,
                                 rng=np.random.default_rng(456))
    assert len(np.unique(pvalues)) > 1
