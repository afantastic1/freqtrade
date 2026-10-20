"""Direction A（预注册执行）的单元测试。全部使用合成数据。"""

import numpy as np
import pandas as pd
import pytest

from alpha_lab import regime_cli
from alpha_lab.data.dataset import MarketData
from alpha_lab.stats.verdict import VerdictPolicy


def make_market(bars: int = 8000, start: str = "2022-01-01") -> MarketData:
    index = pd.date_range(start, periods=bars, freq="1h", tz="UTC")
    rng = np.random.default_rng(11)
    price = 20000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, bars)))

    frame = pd.DataFrame(
        {
            "open_time": index,
            "close_time": index + pd.Timedelta(hours=1),
            "available_time": index + pd.Timedelta(hours=1, seconds=1),
            "open": price,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": rng.uniform(1.0, 10.0, bars),
        }
    )
    return MarketData(symbol="BTC/USDT:USDT", timeframe="1h", frame=frame)


def truncate(market: MarketData, bars: int) -> MarketData:
    return MarketData(
        symbol=market.symbol,
        timeframe=market.timeframe,
        frame=market.frame.iloc[:bars].copy(),
    )


def test_efficiency_ratio_in_unit_interval_and_causal():
    market = make_market(1200)
    full = regime_cli.efficiency_ratio(market, periods=24)
    prefix = regime_cli.efficiency_ratio(truncate(market, 600), periods=24)

    valid = full.dropna()
    assert len(valid) > 0
    assert (valid >= -1e-12).all() and (valid <= 1 + 1e-12).all()
    # 因果性：截断未来数据不改变重叠区间的取值
    np.testing.assert_allclose(
        prefix.iloc[:600].dropna().to_numpy(),
        full.iloc[:600].dropna().to_numpy(),
        rtol=0,
        atol=1e-15,
    )


def test_build_dataset_regime_is_causal():
    config = {
        "id": "T-1",
        "timeframe": "1h",
        "horizon": 4,
        "regime_kind": "log_volatility",
        "regime_window": 24,
        "regime_label": "log_vol24",
        "role": "confirmatory",
        "preregistered_sign": +1,
    }
    market = make_market(1500)
    full = regime_cli.build_dataset(market, config)
    prefix = regime_cli.build_dataset(truncate(market, 900), config)

    assert set(["rev4", "mom24", "regime", "reference_return", "open_time"]) <= set(
        full.columns
    )
    overlap = min(len(prefix), 900)
    np.testing.assert_allclose(
        full["regime"].iloc[:overlap].to_numpy(dtype=float),
        prefix["regime"].iloc[:overlap].to_numpy(dtype=float),
        rtol=0,
        atol=1e-15,
        equal_nan=True,
    )


def test_zscore_uses_training_parameters():
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0]})
    params = {"a": (2.5, 1.118033988749895)}
    z = regime_cli._zscore(frame, params)
    assert z["z_a"].iloc[0] == pytest.approx(
        (1.0 - 2.5) / 1.118033988749895
    )
    # 用训练段统计量标准化后，训练段均值应接近 0
    assert abs(float(z["z_a"].mean())) < 1e-9


def test_stratified_rank_ic_detects_constructed_interaction():
    rng = np.random.default_rng(5)
    n = 3000
    months = pd.date_range("2023-01-01", periods=12, freq="MS", tz="UTC")
    open_time = [
        months[i % 12] + pd.Timedelta(hours=int(i))
        for i in range(n)
    ]
    signal = rng.normal(size=n)
    regime = rng.uniform(-1.0, 1.0, n)

    high = regime > 0.0
    label = np.where(high, 2.0 * signal, -2.0 * signal) + rng.normal(0.0, 1.0, n)

    test = pd.DataFrame(
        {
            "open_time": pd.to_datetime(pd.Series(open_time), utc=True),
            "rev4": signal,
            "reference_return": label,
            "regime": regime,
        }
    )

    result = regime_cli.stratified_rank_ic(
        test,
        signal="rev4",
        label="reference_return",
        regime="regime",
        threshold=0.0,
        horizon=4,
        n_boot=200,
        random_state=0,
    )
    assert result["point"] > 0.5
    assert result["ci_95_lower"] > 0.0
    assert result["monthly_mean_difference"] > 0.0
    assert result["monthly_months"] >= 10


def _synthetic_result(
    config_id: str,
    *,
    auc_point: float,
    auc_p: float,
    auc_ci: tuple[float, float],
    boot_sd: float,
    prereg_sign: int,
    monthly_difference: float,
    monthly_negative_ratio: float,
) -> dict:
    def metric(name, point, ci, boot):
        return {
            "metric": name,
            "point": point,
            "ci_95_lower": ci[0],
            "ci_95_upper": ci[1],
            "boot_sd": boot,
            "p_two_sided": auc_p if name == "auc" else 0.5,
        }

    evidence = {
        "sign_test_p": 1e-6,
        "monthly_negative_ratio": monthly_negative_ratio,
        "n_months": 24,
    }
    return {
        "config": {
            "id": config_id,
            "timeframe": "5m",
            "horizon": 24,
            "regime_kind": "efficiency_ratio",
            "regime_window": 24,
            "regime_label": "ER24",
            "role": "confirmatory",
            "preregistered_sign": prereg_sign,
        },
        "n_train": 1000,
        "n_test": 5000,
        "block_size": 24,
        "block_rule": "max(horizon=24, regime_window=24)",
        "n_eff": {"design": 500.0, "autocorr": 600.0, "variance_inflation": 2.0,
                  "max_lag": 1},
        "metrics": {
            "auc": metric("auc", auc_point, auc_ci, boot_sd),
            "logloss": metric("logloss", 0.0, (-0.01, 0.01), 0.1),
            "brier": metric("brier", 0.0, (-0.005, 0.005), 0.05),
        },
        "mde_auc_bootstrap": 2.80158 * boot_sd,
        "stability": {"positive_ratio": 0.7},
        "redundancy": {"max_abs_spearman": 0.2, "per_factor": {}},
        "regime_switches": 100,
        "regime_mean_dwell_rows": 5.0,
        "mechanism": {
            "monthly_mean_difference": monthly_difference,
            "monthly_negative_ratio": monthly_negative_ratio,
            "monthly_months": 24,
            "point": monthly_difference,
            "ci_95_lower": monthly_difference - 0.01,
            "ci_95_upper": monthly_difference + 0.01,
        },
        "mechanism_evidence": evidence,
        "unconditional_reversal_rank_ic": {"point": 0.03},
        "block_sensitivity": {},
    }


def test_finalize_applies_bh_and_method_gate_to_both_axes():
    # 数值证据看似通过，但当前配对 MBB 方法是 LEGACY，正式结论必须关闭。
    supported = _synthetic_result(
        "A-1",
        auc_point=0.02,
        auc_p=0.0005,
        auc_ci=(0.01, 0.03),
        boot_sd=0.001,
        prereg_sign=+1,
        monthly_difference=0.02,
        monthly_negative_ratio=0.1,
    )
    # 有害方向也不能由未获准的推断方法正式拒绝。
    harmful = _synthetic_result(
        "A-2",
        auc_point=-0.02,
        auc_p=0.0005,
        auc_ci=(-0.03, -0.01),
        boot_sd=0.001,
        prereg_sign=+1,
        monthly_difference=-0.02,
        monthly_negative_ratio=0.9,
    )
    payload = regime_cli.finalize([supported, harmful], VerdictPolicy())

    assert payload["family_size"] == 2
    assert supported["verdict"]["label"] == "INCONCLUSIVE"
    assert supported["overall_verdict"]["label"] == "INCONCLUSIVE"
    assert harmful["verdict"]["label"] == "INCONCLUSIVE"
    assert harmful["overall_verdict"]["label"] == "INCONCLUSIVE"
    assert "INFERENCE_METHOD_NOT_APPROVED" in supported["overall_verdict"]["reasons"]
    assert supported["mechanism_verdict"]["label"] == "INCONCLUSIVE"
    # BH 校正必须不小于原始 p
    for result in payload["results"]:
        assert result["adjusted_p_within_direction"] >= result["p_auc_raw"] - 1e-12
    # 阈值敏感性记录了全部 5 档
    assert set(supported["threshold_sensitivity"]) == {"x0.5", "x0.8", "x1.0",
                                                       "x1.5", "x2.0"}


def test_direction_a_overall_and_monthly_conflict_is_inconclusive():
    result = _synthetic_result(
        "A-1", auc_point=0.02, auc_p=0.0005,
        auc_ci=(0.01, 0.03), boot_sd=0.001,
        prereg_sign=+1, monthly_difference=0.02,
        monthly_negative_ratio=0.1,
    )
    result["mechanism"]["point"] = -0.03
    result["mechanism_evidence"]["observed_sign"] = -1
    result["mechanism_evidence"]["monthly_observed_sign"] = 1
    regime_cli.finalize([result], VerdictPolicy())
    assert result["mechanism_verdict"]["label"] == "INCONCLUSIVE"
    assert result["overall_verdict"]["label"] != "SUPPORTED"


def test_run_config_end_to_end_smoke(monkeypatch):
    market = make_market(4000)
    monkeypatch.setattr(regime_cli, "TRAIN", ("2022-01-01", "2022-03-01"))
    monkeypatch.setattr(regime_cli, "TEST", ("2022-04-01", "2022-06-01"))

    config = {
        "id": "T-1",
        "timeframe": "1h",
        "horizon": 4,
        "regime_kind": "efficiency_ratio",
        "regime_window": 24,
        "regime_label": "ER24",
        "role": "confirmatory",
        "preregistered_sign": +1,
    }
    result = regime_cli.run_config(
        market, config, n_boot=200, rank_ic_boot=100, random_state=0
    )
    assert result["n_train"] > 500
    assert result["n_test"] > 500
    assert set(result["metrics"]) == {"auc", "logloss", "brier"}
    assert result["block_size"] >= 24

    payload = regime_cli.finalize([result], VerdictPolicy())
    payload.update(
        {
            "method_version": regime_cli.METHOD_VERSION,
            "not_run": list(regime_cli.NOT_RUN),
            "splits": {"train": list(regime_cli.TRAIN),
                       "test": list(regime_cli.TEST)},
            "data": {"symbol": "BTC/USDT:USDT", "timeframe": "1h",
                     "range": ["2022-01-01", "2022-06-01"],
                     "sealed": "2025-01-01+"},
            "n_boot": 200,
            "rank_ic_boot": 100,
            "random_state": 0,
        }
    )
    markdown = regime_cli.render_markdown(payload)
    assert "预注册实验执行报告 A" in markdown
    assert "A-3" in markdown  # NOT_RUN 必须出现在报告里
    assert payload["results"][0]["overall_verdict"]["label"] in {
        "SUPPORTED", "REJECTED", "INCONCLUSIVE",
    }
