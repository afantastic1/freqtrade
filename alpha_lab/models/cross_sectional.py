"""Deterministic ridge baseline for relative-return ranking."""

import numpy as np
import pandas as pd


def ridge_predict(train: pd.DataFrame, test: pd.DataFrame,
                  feature_names: tuple[str, ...], *, alpha: float = 1.0,
                  target: str = "relative_return") -> pd.Series:
    if train.empty or test.empty or not feature_names or alpha < 0:
        raise ValueError("ridge requires nonempty train/test/features and alpha >= 0")
    x_train = train.loc[:, feature_names].to_numpy(dtype=float)
    x_test = test.loc[:, feature_names].to_numpy(dtype=float)
    y_train = train[target].to_numpy(dtype=float)
    if not (np.isfinite(x_train).all() and np.isfinite(x_test).all() and np.isfinite(y_train).all()):
        raise ValueError("ridge inputs must be finite")
    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0)
    std = np.where(std > 0, std, 1.0)
    standardized = (x_train - mean) / std
    target_mean = float(y_train.mean())
    gram = standardized.T @ standardized + alpha * np.eye(len(feature_names))
    weights = np.linalg.solve(gram, standardized.T @ (y_train - target_mean))
    predicted = target_mean + (x_test - mean) / std @ weights
    return pd.Series(predicted, index=test.index, name="predicted_relative_return")
