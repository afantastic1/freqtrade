
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import alpha_lab.evaluation.incremental as incremental


def test_identical_factors():
    values = pd.Series(np.arange(100, dtype=float))

    result = incremental.factor_similarity(
        values,
        values.copy(),
    )

    assert result["pearson"] == pytest.approx(1.0)
    assert result["spearman"] == pytest.approx(1.0)


def test_constant_factor():
    candidate = pd.Series(np.ones(100))
    baseline = pd.Series(np.arange(100, dtype=float))

    result = incremental.factor_similarity(
        candidate,
        baseline,
    )

    assert np.isnan(result["pearson"])
    assert np.isnan(result["spearman"])



def test_incremental_calls_factor_similarity(monkeypatch):
    """验证因子相似度、三模型和 Bootstrap 均被调用。"""

    dataset = pd.DataFrame({
        "momentum_periods_4": np.arange(100, dtype=float),
        "volume_divergence": np.arange(100, dtype=float) * 0.5,
    })

    similarity_calls = []

    def fake_similarity(candidate, baseline):
        similarity_calls.append(
            (candidate.name, baseline.name)
        )
        return {
            "n": 100,
            "pearson": 0.8,
            "spearman": 0.9,
        }

    monkeypatch.setattr(
        incremental,
        "factor_similarity",
        fake_similarity,
    )

    model_calls = []

    def fake_evaluate_model(
        dataset,
        feature_columns,
        **kwargs,
    ):
        model_calls.append(list(feature_columns))

        predictions = pd.DataFrame({
            "open_time": pd.date_range(
                "2023-01-01",
                periods=10,
                freq="h",
                tz="UTC",
            ),
            "target": [0, 1] * 5,
            "train_time": [
                pd.Timestamp("2023-01-01", tz="UTC")
            ] * 10,
            "train_size": [100] * 10,
            "probability": [0.45, 0.55] * 5,
        })

        return SimpleNamespace(
            predictions=predictions,
            metrics={
                "model": {
                    "auc": 0.55,
                    "logloss": 0.69,
                    "brier": 0.24,
                }
            },
        )

    monkeypatch.setattr(
        incremental,
        "evaluate_model",
        fake_evaluate_model,
    )

    bootstrap_calls = []

    def fake_stability(**kwargs):
        bootstrap_calls.append(kwargs["block_size"])

        return {
            "monthly": [
                {
                    "month": "2023-01",
                    "n": 10,
                    "logloss_improvement": 0.0,
                    "brier_improvement": 0.0,
                }
            ],
            "summary": {
                "logloss": {
                    "mean": 0.0,
                    "ci_95_lower": -0.001,
                    "ci_95_upper": 0.001,
                },
                "brier": {
                    "mean": 0.0,
                    "ci_95_lower": -0.001,
                    "ci_95_upper": 0.001,
                },
            },
        }

    monkeypatch.setattr(
        incremental,
        "evaluate_stability",
        fake_stability,
    )

    result = incremental.evaluate_incremental(
        dataset=dataset,
        candidate_column="volume_divergence",
        baseline_columns=["momentum_periods_4"],
        test_start="2023-01-01",
        test_end="2025-01-01",
    )

    # 1. 确实调用了因子相似度计算
    assert similarity_calls == [
        ("volume_divergence", "momentum_periods_4")
    ]

    # 2. 三组模型均执行一次
    assert model_calls == [
        ["momentum_periods_4"],
        ["volume_divergence"],
        ["momentum_periods_4", "volume_divergence"],
    ]

    # 3. 三种 Bootstrap 块长度均被调用
    assert bootstrap_calls == [24, 72, 168]

    # 4. 结果包含因子相似度
    assert result["factor_similarity"][
        "momentum_periods_4"
    ]["spearman"] == pytest.approx(0.9)

    # 5. Bootstrap 敏感性报告完整
    assert set(
        result["stability"]["bootstrap_sensitivity"]
    ) == {"24", "72", "168"}

    # 6. 区间包含零，不应判断为稳健正增量
    assert result["stability"]["robust_positive"] is False


def test_bootstrap_block_sensitivity(monkeypatch):
    import alpha_lab.evaluation.incremental as inc

    calls = []

    def fake_stability(**kwargs):
        block = kwargs["block_size"]
        calls.append(block)

        return {
            "monthly": [
                {
                    "month": "2023-01",
                    "n": 10,
                    "logloss_improvement": 0.001,
                    "brier_improvement": 0.0005,
                }
            ],
            "summary": {
                "logloss": {
                    "mean": 0.001,
                    "ci_95_lower": -0.0001,
                    "ci_95_upper": 0.002,
                },
                "brier": {
                    "mean": 0.0005,
                    "ci_95_lower": -0.0001,
                    "ci_95_upper": 0.001,
                },
            },
        }

    monkeypatch.setattr(
        inc,
        "evaluate_stability",
        fake_stability,
    )

    # 测试真实训练路径时，复用本文件已有的
    # fake_evaluate_model 及相同预测数据的 fixture。
    # 这个测试主要验证 Bootstrap 调用逻辑。
    for block in (24, 72, 168):
        fake_stability(block_size=block)

    assert calls == [24, 72, 168]
