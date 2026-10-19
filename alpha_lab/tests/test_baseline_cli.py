"""Task 2 基线模块的单元测试（使用合成数据，不读取真实行情）。"""

import numpy as np
import pandas as pd
import pytest

from alpha_lab import baseline_cli
from alpha_lab.data.dataset import MarketData


def make_market(bars: int = 900, start: str = "2022-01-01") -> MarketData:
    index = pd.date_range(start, periods=bars, freq="1h", tz="UTC")
    rng = np.random.default_rng(7)
    price = 20000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.002, bars)))

    frame = pd.DataFrame({
        "open_time": index,
        "close_time": index + pd.Timedelta(hours=1),
        "available_time": index + pd.Timedelta(hours=1, seconds=1),
        "open": price,
        "high": price * 1.001,
        "low": price * 0.999,
        "close": price,
        "volume": rng.uniform(1.0, 10.0, bars),
    })

    return MarketData(symbol="BTC/USDT:USDT", timeframe="1h", frame=frame)


SYNTHETIC_SPLITS = (
    ("train", "2022-01-01", "2022-01-20"),
    ("validation", "2022-01-20", "2022-01-25"),
    ("test", "2022-01-25", "2022-02-07"),
)


@pytest.fixture
def synthetic(monkeypatch):
    monkeypatch.setattr(baseline_cli, "SPLITS", SYNTHETIC_SPLITS)
    return make_market()


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def test_causal_tercile_positions_are_bounded_and_balanced():
    values = np.linspace(0.0, 1.0, 3000) + np.sin(np.arange(3000) / 50.0)

    positions = baseline_cli._causal_tercile_positions(values, 720)

    assert set(np.unique(positions)).issubset({-1.0, 0.0, 1.0})
    # 暖机期内不足 min_periods 的样本为空仓
    assert np.all(positions[:300] == 0.0)
    warm = positions[720:]
    assert abs(warm.mean()) < 0.15
    assert (warm == 1.0).mean() > 0.15
    assert (warm == -1.0).mean() > 0.15


def test_causal_tercile_positions_are_causal():
    values = np.sin(np.arange(3000) / 37.0)

    base = baseline_cli._causal_tercile_positions(values, 720)

    mutated_values = values.copy()
    mutated_values[-1] += 100.0
    mutated = baseline_cli._causal_tercile_positions(mutated_values, 720)

    np.testing.assert_array_equal(base[:-1], mutated[:-1])


def test_causal_tercile_positions_handles_nan():
    values = np.full(2000, np.nan)
    values[1000:] = np.linspace(-1.0, 1.0, 1000)

    positions = baseline_cli._causal_tercile_positions(values, 720)

    assert np.all(positions[:1000] == 0.0)
    assert np.isfinite(positions).all()


def test_annualised_sharpe_zero_variance_is_nan():
    assert np.isnan(baseline_cli._annualised_sharpe(np.ones(10), 100.0))

    returns = np.array([0.01, -0.01, 0.01, -0.01])
    expected = returns.mean() / returns.std(ddof=1) * np.sqrt(100.0)
    assert baseline_cli._annualised_sharpe(returns, 100.0) == pytest.approx(
        expected
    )


def test_json_safe_removes_non_finite_values():
    payload = {
        "ok": 1.5,
        "nan": float("nan"),
        "inf": float("inf"),
        "numpy": np.float64(2.5),
        "count": np.int64(3),
        "flag": np.True_,
        "nested": [float("nan"), {"deep": np.float32(1.0)}],
    }

    safe = baseline_cli._json_safe(payload)

    assert safe["ok"] == 1.5
    assert safe["nan"] is None
    assert safe["inf"] is None
    assert safe["numpy"] == 2.5
    assert safe["count"] == 3
    assert safe["flag"] is True
    assert safe["nested"][0] is None
    assert safe["nested"][1]["deep"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 因子
# --------------------------------------------------------------------------
def test_realized_volatility_is_causal_and_annualised(synthetic):
    vol = baseline_cli.realized_volatility(synthetic, 24)

    assert vol.name == "vol_24"
    assert int(vol.notna().sum()) > 0
    assert (vol.dropna() > 0).all()

    # 只依赖过去：改动未来一根 K 线的价格不应影响该点之前的波动率
    frame = synthetic.frame.copy(deep=True)
    last = frame.index[-1]
    for column in ("open", "high", "low", "close"):
        frame.loc[last, column] *= 1.5
    mutated = MarketData(
        symbol=synthetic.symbol, timeframe="1h", frame=frame
    )
    vol_after = baseline_cli.realized_volatility(mutated, 24)

    pd.testing.assert_series_equal(
        vol.iloc[:-1], vol_after.iloc[:-1]
    )


def test_build_factor_frame_is_aligned_and_reversal_is_negated(synthetic):
    factors = baseline_cli.build_factor_frame(
        synthetic, include_candidate=False
    )

    assert factors.index.equals(synthetic.frame.index)
    assert set(factors.columns) == {
        "mom4",
        "mom12",
        "mom24",
        "rev4",
        "rev24",
        "vol24",
        "vol168",
        "mom4_volnorm",
    }

    np.testing.assert_allclose(
        factors["rev4"].to_numpy(dtype=float),
        -factors["mom4"].to_numpy(dtype=float),
        equal_nan=True,
    )
    # 20 根 K 线之后动量已经是有效值
    assert factors["mom4"].iloc[30:].notna().all()


def test_segment_mask_respects_bounds_and_label_availability():
    market = make_market()
    labels = baseline_cli.forward_returns(market, horizon=4)
    frame = market.frame.assign(
        label_available_time=labels["label_available_time"]
    )

    plain = baseline_cli._segment_mask(
        frame, "2022-01-01", "2022-01-20", enforce_label_availability=False
    )
    strict = baseline_cli._segment_mask(
        frame, "2022-01-01", "2022-01-20", enforce_label_availability=True
    )

    assert int(plain.sum()) == 19 * 24
    # 严格口径把标签尚未在段内可用的尾部若干行剔除
    assert int(strict.sum()) < int(plain.sum())


# --------------------------------------------------------------------------
# 成本敏感性
# --------------------------------------------------------------------------
def test_cost_sensitivity_structure(synthetic):
    factors = baseline_cli.build_factor_frame(
        synthetic, include_candidate=False
    )
    labels = baseline_cli.forward_returns(synthetic, horizon=4)
    frame = synthetic.frame.assign(
        label_available_time=labels["label_available_time"]
    )

    result = baseline_cli.cost_sensitivity(
        factors, synthetic, frame, horizons=(4,)
    )

    assert "causal rolling terciles" in result["position_rule"]
    rows = result["tables"]["4"]

    names = {row["factor"] for row in rows}
    assert "buy_and_hold" in names
    assert "mom4" in names

    for row in rows:
        assert row["n_rebalances"] > 0
        assert 0.0 <= row["mean_turnover_per_rebalance"] <= 2.0
        assert -1.0 <= row["mean_position"] <= 1.0
        assert "net_annual_sharpe_0bps" in row
        assert "mean_forward_return_bps" in row

    # 测试段 13 天 = 312 小时，每 4 根再平衡一次 -> 78 个不重叠窗口
    assert rows[0]["n_rebalances"] == pytest.approx(78, abs=3)


def test_cost_sensitivity_is_monotone_in_cost(synthetic):
    factors = baseline_cli.build_factor_frame(
        synthetic, include_candidate=False
    )
    labels = baseline_cli.forward_returns(synthetic, horizon=4)
    frame = synthetic.frame.assign(
        label_available_time=labels["label_available_time"]
    )

    rows = baseline_cli.cost_sensitivity(
        factors, synthetic, frame, horizons=(4,)
    )["tables"]["4"]

    ordered = [
        row for row in rows if row["family"] != "reference"
    ]
    assert ordered

    for row in ordered:
        sharpe_0 = row["net_annual_sharpe_0bps"]
        sharpe_10 = row["net_annual_sharpe_10bps"]

        if row["mean_turnover_per_rebalance"] == 0.0:
            # 零换手 -> 成本无影响
            assert sharpe_10 == pytest.approx(sharpe_0)
        else:
            assert sharpe_10 < sharpe_0

    # 参考行换手为 0，因此各成本档位完全一致
    reference = [row for row in rows if row["family"] == "reference"]
    assert reference
    assert reference[0]["mean_turnover_per_rebalance"] == 0.0
    assert reference[0]["breakeven_cost_bps"] is None


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def test_build_highlights_reads_numbers_without_crashing():
    payload = {
        "horizon": 4,
        "split_rank_ic": [
            {
                "factor": "mom4",
                "family": "momentum",
                "split": "test",
                "point": -0.065,
                "ci_95_lower": -0.09,
                "ci_95_upper": -0.04,
            },
            {
                "factor": "rev4",
                "family": "reversal",
                "split": "test",
                "point": 0.065,
                "ci_95_lower": 0.04,
                "ci_95_upper": 0.09,
            },
            {
                "factor": "vol24",
                "family": "volatility",
                "split": "test",
                "point": 0.01,
                "ci_95_lower": -0.02,
                "ci_95_upper": 0.04,
            },
        ],
        "horizon_sweep": [{"horizon": 1}, {"horizon": 24}],
        "volatility_state": {
            "per_factor": {
                "mom4": {
                    "difference": {
                        "ci_95_lower": -0.04,
                        "ci_95_upper": 0.05,
                    }
                }
            }
        },
        "cost_sensitivity": {
            "tables": {
                "4": [
                    {
                        "factor": "buy_and_hold",
                        "family": "reference",
                        "mean_position": 1.0,
                        "gross_annual_sharpe": 1.0,
                        "net_annual_sharpe_0bps": 1.0,
                        "net_annual_sharpe_2bps": 1.0,
                        "net_annual_sharpe_5bps": 1.0,
                        "breakeven_cost_bps": None,
                    },
                    {
                        "factor": "mom4",
                        "family": "momentum",
                        "mean_position": 0.02,
                        "gross_annual_sharpe": 0.5,
                        "net_annual_sharpe_0bps": 0.5,
                        "net_annual_sharpe_2bps": 0.1,
                        "net_annual_sharpe_5bps": -0.5,
                        "breakeven_cost_bps": 3.2,
                    },
                ]
            }
        },
    }

    bullets = baseline_cli.build_highlights(payload)

    assert isinstance(bullets, list) and bullets
    assert all(isinstance(item, str) and item for item in bullets)
    joined = " ".join(bullets)
    assert "mom4" in joined
    assert "2bps 下 1 个" in joined


def test_render_markdown_contains_all_sections():
    payload = {
        "timeframe": "1h",
        "horizon": 4,
        "window": ["2022-01-01", "2025-01-01"],
        "n_boot": 10,
        "random_state": 42,
        "reproduction": {
            "run_id": "0dc03df9811645b5a5aeb11191c669a9",
            "timeframe": "1h",
            "horizon": 4,
            "window": ["2022-01-01", "2025-01-01"],
            "all_identical": True,
            "comparisons": [
                {
                    "factor": "mom4",
                    "stored": {"ic": 0.1, "rank_ic": 0.1, "valid": 10,
                               "negative_rank_ic_ratio": 0.5,
                               "mean_monthly_rank_ic": 0.1},
                    "recomputed": {"ic": 0.1, "rank_ic": 0.1, "valid": 10,
                                   "negative_rank_ic_ratio": 0.5,
                                   "mean_monthly_rank_ic": 0.1},
                    "abs_diff": {"ic": 0.0, "rank_ic": 0.0, "valid": 0.0,
                                 "negative_rank_ic_ratio": 0.0,
                                 "mean_monthly_rank_ic": 0.0},
                }
            ],
        },
        "split_rank_ic": [],
        "horizon_sweep": [],
        "volatility_state": {
            "regime_variable": "vol24",
            "threshold_train_median": 0.5,
            "threshold_source": "train segment only",
            "per_factor": {},
        },
        "cost_sensitivity": {
            "split": "test",
            "position_rule": "train-estimated terciles",
            "cost_levels_bps": [0.0],
            "tables": {"4": []},
        },
        "highlights": ["示例读数"],
    }

    text = baseline_cli.render_markdown(payload)

    for section in (
        "## 1. 复现校验",
        "## 2. 时间顺序切分下的 RankIC",
        "## 3. horizon 扫描",
        "## 4. 波动率状态条件化",
        "## 5. 交易成本敏感性",
        "## 6. 关键读数",
    ):
        assert section in text
