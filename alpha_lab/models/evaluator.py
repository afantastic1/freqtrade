
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from sklearn.base import BaseEstimator
from sklearn.metrics import (
    roc_auc_score,
    log_loss,
    brier_score_loss,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from alpha_lab.validation.walk_forward import make_walk_forward


@dataclass
class ModelEvaluationResult:
    predictions: pd.DataFrame
    metrics: dict
    fold_metrics: pd.DataFrame


def classification_metrics(y, p) -> dict:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)

    if (
        len(y) == 0
        or not np.isin(y, [0, 1]).all()
        or not np.isfinite(p).all()
        or ((p < 0) | (p > 1)).any()
    ):
        raise ValueError("Invalid classification inputs")

    auc = (
        float(roc_auc_score(y, p))
        if len(np.unique(y)) == 2
        else np.nan
    )

    return {
        "auc": auc,
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "n": len(y),
    }


def evaluate_model(
    dataset: pd.DataFrame,
    feature_columns: list[str],
    model_factory: Callable[[], BaseEstimator],
    *,
    test_start: str,
    test_end: str,
    train_months: int = 12,
    retrain_months: int = 1,
    min_train_samples: int = 1000,
    scale_features: bool = True,
    sample_eligible: pd.Series | None = None
) -> ModelEvaluationResult:
    """
    通用二分类模型 Walk-Forward 评价。

    model_factory 每个训练折必须返回全新的模型实例。
    """

    predictions = []
    fold_scores = []

    folds = make_walk_forward(
        data=dataset,
        feature_columns=feature_columns,
        test_start=test_start,
        test_end=test_end,
        train_months=train_months,
        retrain_months=retrain_months,
        min_train_samples=min_train_samples,
        sample_eligible=sample_eligible,
    )

    for fold in folds:
        train = dataset.loc[fold.train_idx]
        test = dataset.loc[fold.test_idx]

        # 防止标签和因子使用未来信息
        if not (
            train["label_available_time"] <= fold.train_time
        ).all():
            raise ValueError("Training label leakage detected")

        if not (
            train["decision_time"] <= fold.train_time
        ).all():
            raise ValueError("Training feature leakage detected")

        if not (
            test["decision_time"] >= fold.train_time
        ).all():
            raise ValueError("Invalid test decision time")

        X_train = train[feature_columns]
        y_train = train["target"].astype(int)

        X_test = test[feature_columns]
        y_test = test["target"].astype(int)

        if y_train.nunique() != 2:
            raise ValueError(
                f"{fold.month}: training requires two classes"
            )

        model = model_factory()

        # 只有需要标准化的模型才使用 StandardScaler。
        # StandardScaler 仅在训练折内 fit。
        if scale_features:
            model = make_pipeline(
                StandardScaler(),
                model,
            )

        model.fit(X_train, y_train)

        if not hasattr(model, "predict_proba"):
            raise TypeError(
                "Classifier must implement predict_proba"
            )

        probability = model.predict_proba(X_test)[:, 1]

        # 历史上涨比例作为概率预测基线
        baseline = float(y_train.mean())

        result = test[
            [
                "open_time",
                "decision_time",
                "target",
            ]
        ].copy()

        result["probability"] = probability
        result["baseline"] = baseline
        result["fold"] = fold.month
        result["train_time"] = fold.train_time
        result["train_size"] = len(train)

        predictions.append(result)

        score = classification_metrics(
            y_test,
            probability,
        )

        baseline_score = classification_metrics(
            y_test,
            np.full(len(y_test), baseline),
        )

        fold_scores.append({
            "fold": fold.month,
            "train_size": len(train),
            "test_size": len(test),
            "auc": score["auc"],
            "logloss": score["logloss"],
            "brier": score["brier"],
            "baseline_logloss": baseline_score["logloss"],
            "baseline_brier": baseline_score["brier"],
        })

    if not predictions:
        raise ValueError("No valid Walk-Forward folds")

    all_predictions = pd.concat(
        predictions,
        ignore_index=True,
    )

    if all_predictions["open_time"].duplicated().any():
        raise ValueError("Duplicate prediction timestamps")

    y = all_predictions["target"].to_numpy(dtype=int)
    p = all_predictions["probability"].to_numpy(dtype=float)
    b = all_predictions["baseline"].to_numpy(dtype=float)

    model_score = classification_metrics(y, p)
    baseline_score = classification_metrics(y, b)

    summary = {
        "model": model_score,
        "baseline": baseline_score,
        "improvement": {
            "logloss": (
                baseline_score["logloss"]
                - model_score["logloss"]
            ),
            "brier": (
                baseline_score["brier"]
                - model_score["brier"]
            ),
        },
    }

    return ModelEvaluationResult(
        predictions=all_predictions,
        metrics=summary,
        fold_metrics=pd.DataFrame(fold_scores),
    )
