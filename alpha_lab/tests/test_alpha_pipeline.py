
import numpy as np
import pandas as pd
import pytest

from alpha_lab.data.dataset import MarketData
from alpha_lab.factors.momentum import momentum
from alpha_lab.labels.forward_returns import forward_returns
from alpha_lab.evaluation.ic import evaluate_ic


# ============================================================
# 测试数据构造
# ============================================================

def make_market_data(
    closes=None,
    hours=None,
    availability_delay=1,
):
    """构造 BTCUSDT 1h 测试行情。"""

    if closes is None:
        closes = [100, 102, 104, 106, 110, 112, 114, 116]

    if hours is None:
        hours = list(range(len(closes)))

    assert len(closes) == len(hours)

    base = pd.Timestamp("2024-01-01T00:00:00Z")
    rows = []

    for hour, close in zip(hours, closes):
        start = base + pd.Timedelta(hours=hour)
        end = start + pd.Timedelta(hours=1)

        rows.append({
            "open_time": start,
            "close_time": end,
            "available_time": (
                end + pd.Timedelta(seconds=availability_delay)
            ),
            "open": float(close),
            "high": float(close) + 2,
            "low": float(close) - 2,
            "close": float(close),
            "volume": 100.0,
        })

    return MarketData(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        frame=pd.DataFrame(rows),
    )


# ============================================================
# Part 1: Momentum
# ============================================================

def test_momentum_value():
    """4 根 K 线动量应与手工计算一致。"""

    data = make_market_data(
        closes=[100, 102, 104, 106, 110]
    )

    factor = momentum(data, periods=4)

    assert np.isnan(factor.iloc[0])
    assert np.isnan(factor.iloc[1])
    assert np.isnan(factor.iloc[2])
    assert np.isnan(factor.iloc[3])

    assert factor.iloc[4] == pytest.approx(0.10)


def test_momentum_missing_candle():
    """缺失 K 线时不能跨越缺口计算动量。"""

    data = make_market_data(
        closes=[100, 102, 104, 110, 112, 114],
        hours=[0, 1, 2, 4, 5, 6],
    )

    factor = momentum(data, periods=4)

    # 最早可以得到结果的位置也跨越了缺口
    assert np.isnan(factor.iloc[4])
    assert np.isnan(factor.iloc[5])


def test_momentum_no_future_leakage():
    """增加未来数据，不应改变过去的因子值。"""

    full = make_market_data(
        closes=[100, 102, 104, 106, 110, 112, 114]
    )

    partial = MarketData(
        symbol=full.symbol,
        timeframe=full.timeframe,
        frame=full.frame.iloc[:5].copy(),
    )

    factor_full = momentum(full, periods=4)
    factor_partial = momentum(partial, periods=4)

    pd.testing.assert_series_equal(
        factor_full.iloc[:5],
        factor_partial,
    )


def test_momentum_invalid_period():
    data = make_market_data()

    with pytest.raises(ValueError, match="periods must be positive"):
        momentum(data, periods=0)


# ============================================================
# Part 2: Forward Returns
# ============================================================

def test_forward_returns_value():
    """验证 next-open 到第 4 根未来 K 线收盘的收益。"""

    data = make_market_data(
        closes=[100, 102, 104, 106, 110, 112]
    )

    labels = forward_returns(data, horizon=4)

    # t=0 时：
    # entry = open[1] = 102
    # exit  = close[4] = 110
    expected = 110 / 102 - 1

    assert labels["reference_return"].iloc[0] == pytest.approx(
        expected
    )

    assert labels["reference_return"].iloc[1] == pytest.approx(
        112 / 104 - 1
    )


def test_forward_returns_tail_nan():
    """未来不足 4 根 K 线时，不得生成标签。"""

    data = make_market_data(
        closes=[100, 102, 104, 106, 110, 112]
    )

    labels = forward_returns(data, horizon=4)

    assert labels["reference_return"].iloc[:2].notna().all()
    assert labels["reference_return"].iloc[2:].isna().all()


def test_forward_returns_missing_candle():
    """未来窗口内缺失 K 线时，标签必须无效。"""

    data = make_market_data(
        closes=[100, 102, 104, 110, 112, 114, 116],
        hours=[0, 1, 2, 4, 5, 6, 7],
    )

    labels = forward_returns(data, horizon=4)

    # t=0 未来窗口包含 2h -> 4h 的缺口
    assert np.isnan(labels["reference_return"].iloc[0])

    # t=1 同样跨越缺口
    assert np.isnan(labels["reference_return"].iloc[1])

    # t=3 的未来样本不足 4 根，标签无效
    assert np.isnan(labels["reference_return"].iloc[3])


def test_forward_returns_one_second_delay():
    """晚于 next-open 才获取数据，不能视为可执行入场。"""

    data = make_market_data(
        availability_delay=1
    )

    labels = forward_returns(data, horizon=4)

    # 理想化研究标签仍然存在
    assert np.isfinite(labels["reference_return"].iloc[0])

    # 但无法在 next-open 时成交
    assert not bool(labels["entry_time_feasible"].iloc[0])


def test_forward_returns_zero_delay():
    """零延迟时仅检查时间先后关系。"""

    data = make_market_data(
        availability_delay=0
    )

    labels = forward_returns(data, horizon=4)

    assert bool(labels["entry_time_feasible"].iloc[0])

    # 时间可行不等于真实订单一定成交


def test_forward_returns_label_end():
    """验证标签结束时刻。"""

    data = make_market_data()

    labels = forward_returns(data, horizon=4)

    expected_end = pd.Timestamp(
        "2024-01-01T05:00:00Z"
    )

    assert labels["label_end_time"].iloc[0] == expected_end


def test_forward_returns_invalid_horizon():
    data = make_market_data()

    with pytest.raises(ValueError, match="horizon must be positive"):
        forward_returns(data, horizon=0)


# ============================================================
# Part 3: IC Evaluation
# ============================================================

def test_ic_perfect_positive():
    """完全正相关时 IC 和 RankIC 均为 1。"""

    factor = pd.Series([1, 2, 3, 4, 5], dtype=float)
    label = pd.Series([2, 4, 6, 8, 10], dtype=float)

    result = evaluate_ic(factor, label)

    assert result["ic"] == pytest.approx(1.0)
    assert result["rank_ic"] == pytest.approx(1.0)
    assert result["n_obs"] == 5


def test_ic_perfect_negative():
    """完全负相关时 IC 和 RankIC 均为 -1。"""

    factor = pd.Series([1, 2, 3, 4, 5], dtype=float)
    label = pd.Series([10, 8, 6, 4, 2], dtype=float)

    result = evaluate_ic(factor, label)

    assert result["ic"] == pytest.approx(-1.0)
    assert result["rank_ic"] == pytest.approx(-1.0)


def test_ic_drop_invalid_values():
    """NaN 和 Inf 不应参与 IC 计算。"""

    factor = pd.Series(
        [1, 2, 3, 4, 5, np.inf],
        dtype=float,
    )

    label = pd.Series(
        [2, 4, 6, np.nan, 10, 12],
        dtype=float,
    )

    result = evaluate_ic(factor, label)

    assert result["n_obs"] == 4
    assert result["ic"] == pytest.approx(1.0)


def test_ic_constant_factor():
    """常量因子没有可定义的相关系数。"""

    factor = pd.Series([1, 1, 1, 1, 1], dtype=float)
    label = pd.Series([1, 2, 3, 4, 5], dtype=float)

    with pytest.raises(ValueError, match="Constant factor"):
        evaluate_ic(factor, label)


def test_ic_index_mismatch():
    """因子与标签索引不一致时必须失败。"""

    factor = pd.Series(
        [1, 2, 3],
        index=[0, 1, 2],
    )

    label = pd.Series(
        [2, 4, 6],
        index=[1, 2, 3],
    )

    with pytest.raises(
        ValueError,
        match="Factor and label indexes differ",
    ):
        evaluate_ic(factor, label)


def test_ic_insufficient_samples():
    """有效观测少于 3 个时不计算 IC。"""

    factor = pd.Series([1.0, 2.0, np.nan])
    label = pd.Series([2.0, 4.0, 6.0])

    with pytest.raises(
        ValueError,
        match="Not enough valid observations",
    ):
        evaluate_ic(factor, label)


# ============================================================
# Part 4: Data Integrity
# ============================================================

def test_pipeline_does_not_modify_market_data():
    """因子和标签计算不得修改原始行情。"""

    data = make_market_data()
    before = data.frame.copy(deep=True)

    momentum(data, periods=4)
    forward_returns(data, horizon=4)

    pd.testing.assert_frame_equal(
        data.frame,
        before,
    )


def test_label_available_time():
    """标签可用时间必须来自未来K线的 available_time。"""

    data = make_market_data(
        closes=[100, 102, 104, 106, 110, 112],
        availability_delay=3,
    )

    labels = forward_returns(data, horizon=4)

    expected_end = pd.Timestamp(
        "2024-01-01T05:00:00Z"
    )

    expected_available = pd.Timestamp(
        "2024-01-01T05:00:03Z"
    )

    assert labels["label_end_time"].iloc[0] == expected_end

    assert (
        labels["label_available_time"].iloc[0]
        == expected_available
    )

    assert (
        labels["label_available_time"].iloc[0]
        > labels["label_end_time"].iloc[0]
    )


def test_label_available_time_insufficient_future():
    """未来不足4根K线时，不得产生标签及标签可用时间。"""

    data = make_market_data(
        closes=[100, 102, 104, 106],
        availability_delay=3,
    )

    labels = forward_returns(data, horizon=4)

    assert pd.isna(
        labels["reference_return"].iloc[0]
    )

    assert pd.isna(
        labels["label_end_time"].iloc[0]
    )

    assert pd.isna(
        labels["label_available_time"].iloc[0]
    )

def test_label_available_time_variable_delay():
    """标签可用时间必须使用未来K线的实际延迟。"""

    data = make_market_data(
        closes=[100, 102, 104, 106, 110, 112],
        availability_delay=1,
    )

    df = data.frame.copy(deep=True)

    # 未来第4根K线实际延迟10秒才可用
    df.loc[4, "available_time"] = (
        df.loc[4, "close_time"]
        + pd.Timedelta(seconds=10)
    )

    modified = MarketData(
        symbol=data.symbol,
        timeframe=data.timeframe,
        frame=df,
    )

    labels = forward_returns(modified, horizon=4)

    assert labels["label_available_time"].iloc[0] == (
        pd.Timestamp("2024-01-01T05:00:10Z")
    )
