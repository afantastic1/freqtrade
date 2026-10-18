
import numpy as np
import pandas as pd
import pytest

from sklearn.linear_model import LogisticRegression

from alpha_lab.models.evaluator import (
    evaluate_model,
    classification_metrics,
)


def make_dataset():
    rng = np.random.default_rng(42)

    times = pd.date_range(
        "2022-01-01",
        "2023-04-01",
        freq="1h",
        inclusive="left",
        tz="UTC",
    )

    n = len(times)

    factor_a = rng.normal(size=n)
    factor_b = rng.normal(size=n)

    # 构造用于测试的二分类目标
    score = -0.8 * factor_a + 0.3 * factor_b
    target = (score + rng.normal(size=n) > 0).astype(int)

    return pd.DataFrame({
        "open_time": times,
        "decision_time": times + pd.Timedelta(hours=1, seconds=1),
        "label_available_time": (
            times + pd.Timedelta(hours=5, seconds=1)
        ),
        "factor_a": factor_a,
        "factor_b": factor_b,
        "target": target,
    })


def test_classification_metrics():
    y = [0, 0, 1, 1]
    p = [0.1, 0.2, 0.8, 0.9]

    result = classification_metrics(y, p)

    assert result["auc"] == pytest.approx(1.0)
    assert result["brier"] == pytest.approx(0.025)
    assert result["n"] == 4


def test_multifactor_evaluation():
    df = make_dataset()

    result = evaluate_model(
        dataset=df,
        feature_columns=["factor_a", "factor_b"],
        model_factory=lambda: LogisticRegression(
            max_iter=1000
        ),
        test_start="2023-01-01",
        test_end="2023-04-01",
        train_months=12,
        retrain_months=1,
        min_train_samples=1000,
    )

    assert len(result.fold_metrics) == 3
    assert not result.predictions.empty
    assert result.predictions["open_time"].is_unique

    assert result.predictions["probability"].between(
        0, 1
    ).all()

    assert (
        result.predictions["target"].isin([0, 1])
    ).all()


def test_new_model_per_fold():
    df = make_dataset()
    created_models = []

    def factory():
        model = LogisticRegression(max_iter=1000)
        created_models.append(model)
        return model

    result = evaluate_model(
        dataset=df,
        feature_columns=["factor_a", "factor_b"],
        model_factory=factory,
        test_start="2023-01-01",
        test_end="2023-04-01",
        train_months=12,
        min_train_samples=1000,
    )

    assert len(created_models) == 3
    assert len({id(m) for m in created_models}) == 3
    assert len(result.fold_metrics) == 3


def test_future_labels_are_purged():
    df = make_dataset()

    # 某条旧样本的标签在预测开始后才可用
    target_time = pd.Timestamp(
        "2022-12-31T23:00:00Z"
    )

    mask = df["open_time"] == target_time

    df.loc[mask, "label_available_time"] = pd.Timestamp(
        "2023-01-03T00:00:00Z"
    )

    # 当前 Walk-Forward 应能够排除此样本
    result = evaluate_model(
        dataset=df,
        feature_columns=["factor_a", "factor_b"],
        model_factory=lambda: LogisticRegression(
            max_iter=1000
        ),
        test_start="2023-01-01",
        test_end="2023-02-01",
        train_months=12,
        min_train_samples=1000,
    )

    assert not result.predictions.empty
    assert len(result.fold_metrics) == 1
