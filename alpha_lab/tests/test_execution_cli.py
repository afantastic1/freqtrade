"""真实成本执行模型的单元测试。全部使用合成数据。"""

import numpy as np
import pandas as pd
import pytest

from alpha_lab import execution_cli


def test_hold_positions_updates_only_every_horizon():
    positions = np.array([1.0, -1.0, 1.0, 1.0, -1.0, 0.0, 0.0, 0.0, 1.0])
    held = execution_cli.hold_positions(positions, 4)
    # 只在 index % 4 == 0 处更新
    assert held[0] == positions[0]
    assert held[1] == positions[0]
    assert held[2] == positions[0]
    assert held[3] == positions[0]
    assert held[4] == positions[4]
    assert held[8] == positions[8]
    # 换手显著低于逐根更新
    assert np.abs(np.diff(held)).sum() < np.abs(np.diff(positions)).sum()


def test_hysteresis_positions_state_machine():
    rank = pd.Series([0.55, 0.65, 0.58, 0.52, 0.45, 0.35, 0.42, 0.55, 0.70])
    positions = execution_cli.hysteresis_positions(rank)
    # 0.55 在死区 (0.5, 0.6) 内 -> 不开仓
    assert positions[0] == 0.0
    # 0.65 越过入场带 -> 开多
    assert positions[1] == 1.0
    # 0.58 / 0.52 仍在死区之上（> exit 0.5）-> 保持
    assert positions[2] == 1.0
    assert positions[3] == 1.0
    # 0.45 <= exit -> 平仓
    assert positions[4] == 0.0
    # 0.35 越过空头入场带 (<=0.4) -> 开空
    assert positions[5] == -1.0
    # 0.42 未回到 0.5 -> 保持空头
    assert positions[6] == -1.0
    # 0.55 -> 平空
    assert positions[7] == 0.0
    # 0.70 -> 再度开多
    assert positions[8] == 1.0


def test_hysteresis_never_flips_inside_dead_zone():
    rng = np.random.default_rng(0)
    rank = pd.Series(rng.uniform(0.5, 0.6, 500))
    positions = execution_cli.hysteresis_positions(rank)
    assert np.all(positions == 0.0)


def test_causal_rank_is_prefix_invariant():
    rng = np.random.default_rng(1)
    values = pd.Series(rng.normal(size=2000))
    full = execution_cli.causal_rank(values, window=200)
    prefix = execution_cli.causal_rank(values.iloc[:1200], window=200)
    np.testing.assert_allclose(
        prefix.dropna().to_numpy(),
        full.iloc[:1200].dropna().to_numpy(),
        rtol=0,
        atol=1e-15,
    )
    valid = full.dropna()
    assert (valid > 0).all() and (valid <= 1).all()


def _frame_and_returns(bars: int = 200, trend: float = 0.001):
    index = pd.date_range("2022-01-01", periods=bars, freq="1h", tz="UTC")
    open_price = 20000.0 * np.exp(trend * np.arange(bars))
    frame = pd.DataFrame({"open_time": index, "open": open_price,
                          "close": open_price})
    returns = pd.Series(
        np.concatenate([np.diff(open_price) / open_price[:-1], [np.nan]])
    )
    return frame, returns


def test_backtest_accounting_signs():
    frame, returns = _frame_and_returns()
    bars = len(frame)
    funding = np.zeros(bars)
    funding[8::8] = 1e-4  # 正资金费率，每 8 小时结算

    long_positions = np.ones(bars)
    result = execution_cli.backtest(
        long_positions, frame, returns, funding,
        horizon=4, window_start="2022-01-01", window_end="2022-02-01",
    )
    # 常数多头：只在一开始换手一次
    assert result["mean_turnover_per_bar"] < 0.05
    # 正费率下多头付出资金费 -> 资金费为负贡献
    assert result["mean_funding_bps"] > 0
    assert result["net_of_funding_annual_sharpe"] < result["gross_annual_sharpe"]
    # 成本档位越多，净 Sharpe 越低
    assert result["net_annual_sharpe_0bps"] >= result["net_annual_sharpe_20bps"]

    # 反过来做空：正费率应收资金费
    short = execution_cli.backtest(
        -long_positions, frame, returns, funding,
        horizon=4, window_start="2022-01-01", window_end="2022-02-01",
    )
    assert short["mean_funding_bps"] < 0


def test_backtest_includes_costs_on_turnover():
    frame, returns = _frame_and_returns(bars=100)
    funding = np.zeros(len(frame))
    flip = np.array([1.0 if i % 2 == 0 else -1.0 for i in range(len(frame))])
    result = execution_cli.backtest(
        flip, frame, returns, funding,
        horizon=1, window_start="2022-01-01", window_end="2022-02-01",
    )
    assert result["mean_turnover_per_bar"] > 0.5
    # 高换手 + 高成本 -> 盈亏平衡成本很低
    assert result["breakeven_cost_bps_incl_funding"] is not None


def test_json_safe_handles_nan_and_numpy():
    payload = {
        "a": float("nan"),
        "b": np.float64(1.5),
        "c": np.int64(3),
        "d": [np.bool_(True), float("inf")],
        "e": {"f": float("nan")},
    }
    safe = execution_cli._json_safe(payload)
    assert safe["a"] is None
    assert safe["b"] == 1.5
    assert safe["c"] == 3
    assert safe["d"][0] is True
    assert safe["d"][1] is None
    assert safe["e"]["f"] is None


def test_build_positions_mechanisms_are_aligned():
    rng = np.random.default_rng(2)
    values = rng.normal(size=1500)
    signal = pd.Series(values)
    rank = execution_cli.causal_rank(signal, window=200)
    for mechanism in execution_cli.MECHANISMS:
        positions = execution_cli.build_positions(
            signal.to_numpy(), rank, mechanism, horizon=4
        )
        assert positions.shape == signal.shape
        assert np.all(np.abs(positions) <= 1.0)


def test_build_positions_rejects_unknown_mechanism():
    with pytest.raises(ValueError):
        execution_cli.build_positions(
            np.zeros(10), pd.Series(np.zeros(10)), "nope", horizon=4
        )
