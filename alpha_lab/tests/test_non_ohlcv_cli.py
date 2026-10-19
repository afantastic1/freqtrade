"""Direction B（非 OHLCV 因子）的单元测试。全部使用合成数据。"""

import numpy as np
import pandas as pd
import pytest

from alpha_lab import non_ohlcv_cli


def test_bars_per_day():
    assert non_ohlcv_cli.bars_per_day("1h") == 24
    assert non_ohlcv_cli.bars_per_day("5m") == 288


def test_newey_west_vif_iid_is_one():
    rng = np.random.default_rng(0)
    value = non_ohlcv_cli.newey_west_vif(pd.Series(rng.normal(size=5000)), 1)
    assert value == pytest.approx(1.0, abs=0.15)


def test_newey_west_vif_autocorrelated_exceeds_one():
    rng = np.random.default_rng(1)
    n = 5000
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.8 * x[i - 1] + rng.normal(0.0, 0.5)
    series = pd.Series(x)
    # max_lag=1 时 Newey–West 修正 = 1 + ρ1；AR(1) φ=0.8 -> ≈1.8
    lag_one = non_ohlcv_cli.newey_west_vif(series, 1)
    assert lag_one == pytest.approx(1.8, abs=0.15)
    # 允许多阶自相关时膨胀更大
    lag_five = non_ohlcv_cli.newey_west_vif(series, 5)
    assert lag_five > lag_one
    # 上界保护：退化输入返回 nan 而不是崩溃
    assert np.isnan(non_ohlcv_cli.newey_west_vif(pd.Series([1.0]), 1))


def test_newey_west_vif_returns_at_least_one_for_negative_autocorr():
    rng = np.random.default_rng(2)
    n = 5000
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = -0.8 * x[i - 1] + rng.normal(0.0, 0.5)
    value = non_ohlcv_cli.newey_west_vif(pd.Series(x), 1)
    assert value >= 1.0


def _segment_dataset() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    index = pd.date_range("2022-01-01", periods=20000, freq="1h", tz="UTC")
    mom4 = rng.normal(size=index.shape[0])
    mom12 = rng.normal(size=index.shape[0])
    vol24 = rng.uniform(0.001, 0.02, index.shape[0])
    # basis_dev 完全由 mom4 驱动 + 独立噪声
    basis_dev = 2.0 * mom4 + rng.normal(0.0, 0.5, index.shape[0])

    frame = pd.DataFrame(
        {
            "open_time": index,
            "mom4": mom4,
            "mom12": mom12,
            "vol24": vol24,
            "basis": basis_dev * 0.1,
            "basis_dev": basis_dev,
            "basis_change": rng.normal(0.0, 1e-4, index.shape[0]),
            "reference_return": -mom4 * 0.01 + rng.normal(0.0, 0.01, index.shape[0]),
        }
    )
    frame.attrs["window_bars"] = 720
    frame.attrs["missing_spot_bars"] = 1
    return frame


def test_residualize_removes_linear_dependence_on_bases():
    dataset = _segment_dataset()
    residual = non_ohlcv_cli.residualize(
        dataset, "basis_dev", base_columns=["mom4", "mom12", "vol24"]
    )
    train_mask = non_ohlcv_cli._segment_mask(dataset, non_ohlcv_cli.TRAIN)
    train_residual = residual[train_mask]
    train_mom4 = dataset.loc[train_mask, "mom4"]

    correlation = float(np.corrcoef(train_residual, train_mom4)[0, 1])
    assert abs(correlation) < 0.05
    # 残差应保留（甚至放大）与 mom4 无关的那部分
    assert residual.std() > 0


def test_rank_ic_endpoint_is_causal_and_reports_uncertainty():
    dataset = _segment_dataset()
    setting = {"id": "H-B1a", "timeframe": "1h", "horizon": 4, "window_days": 30,
               "role": "primary", "preregistered_sign": -1}
    result = non_ohlcv_cli.rank_ic_endpoint(
        dataset, "basis_dev", setting, n_boot=120, random_state=0
    )
    assert result["n"] > 500
    assert np.isfinite(result["rank_ic"]["point"])
    assert result["rank_ic"]["ci_95_lower"] <= result["rank_ic"]["point"]
    assert result["mde_rank_ic_bootstrap"] > 0
    assert result["n_eff_design"] == pytest.approx(result["n"] / 4, rel=0.05)
    assert set(result["block_sensitivity"]) <= {"1", "3", "8"}


def test_run_direction_b_and_markdown_smoke(monkeypatch):
    dataset = _segment_dataset()
    monkeypatch.setattr(
        non_ohlcv_cli, "build_dataset", lambda *a, **k: dataset.copy()
    )
    monkeypatch.setattr(non_ohlcv_cli, "TRAIN", ("2022-01-01", "2022-03-01"))
    monkeypatch.setattr(non_ohlcv_cli, "TEST", ("2022-04-01", "2022-06-01"))

    payload = non_ohlcv_cli.run_direction_b(n_boot=80, random_state=0)

    assert payload["family_size"] == 3  # H-B1a + H-B1b + H-B4
    assert payload["frozen_family_size"] == 4
    assert {row["id"] for row in payload["not_run"]} == {"H-B2", "H-B3", "OI"}
    for result in payload["results"]:
        assert result["overall_verdict"]["label"] in {
            "SUPPORTED", "REJECTED", "INCONCLUSIVE",
        }
        assert result["adjusted_p_within_direction"] >= result["p_raw"] - 1e-12
    residual = next(
        r for r in payload["results"] if r["setting"]["id"] == "H-B4"
    )
    assert "amplitude_ratio_vs_original" in residual

    markdown = non_ohlcv_cli.render_markdown(payload)
    assert "预注册实验执行报告 B" in markdown
    assert "H-B2" in markdown  # 未运行的检验必须写明原因
