
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    roc_auc_score,
    log_loss,
    brier_score_loss,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset
from alpha_lab.factors.registry import (
    FactorSpec,
    default_registry,
)
from alpha_lab.factors.builder import build_features
from alpha_lab.validation.walk_forward import make_walk_forward


# ============================================================
# 实验配置
# ============================================================

SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "1h"
HORIZON = 4

TRAIN_MONTHS = 12
RETRAIN_MONTHS = 1

DEV_START = pd.Timestamp("2022-01-01", tz="UTC")
DEV_END = pd.Timestamp("2025-01-01", tz="UTC")

TEST_START = "2023-01-01"
TEST_END = "2025-01-01"

# 必须是已隔离的 2022-2024 开发数据
DATA_DIR = Path("user_data/data/binance")

OUTPUT_DIR = Path("user_data/research/alpha_lab")
OUTPUT = OUTPUT_DIR / "rolling_logistic_multi_predictions.csv"

FACTORS = [
    FactorSpec("momentum", {"periods": 4}),
    FactorSpec("momentum", {"periods": 12}),
]


# ============================================================
# 评价指标
# ============================================================

def metrics(y, p):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)

    if len(np.unique(y)) < 2:
        auc = np.nan
    else:
        auc = roc_auc_score(y, p)

    return {
        "auc": float(auc),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
    }


# ============================================================
# 数据准备
# ============================================================

def prepare_dataset():
    if not DATA_DIR.is_dir():
        raise FileNotFoundError(
            f"Development data directory not found: {DATA_DIR}"
        )

    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        datadir=DATA_DIR,
        start="2022-01-01",
        end="2025-01-01",
    )
    if (
    market.frame["open_time"].min()
    < pd.Timestamp("2022-01-01", tz="UTC")
    or market.frame["close_time"].max()
    > pd.Timestamp("2025-01-01", tz="UTC")
    ):
        raise ValueError("Data outside research window")
    # 第二道防线：再次检查实际加载的数据时间范围
    frame = market.frame

    if (
        (frame["open_time"] < DEV_START).any()
        or (frame["close_time"] > DEV_END).any()
    ):
        raise ValueError("Data outside development period")

    # 1. 根据配置计算任意数量的因子
    registry = default_registry()

    features = build_features(
        market=market,
        specs=FACTORS,
        registry=registry,
    )

    # 2. 因子矩阵和标签组合
    df = build_research_dataset(
        market=market,
        features=features,
        horizon=HORIZON,
    )

    df = df.sort_values("open_time").reset_index(drop=True)

    feature_columns = features.columns.tolist()

    print("\n=== FEATURES ===")
    print(feature_columns)

    print("\n=== DATASET ===")
    print("Rows:", len(df))
    print("Valid targets:", df["target"].notna().sum())
    print("First:", df["open_time"].iloc[0])
    print("Last:", df["open_time"].iloc[-1])

    return df, feature_columns


# ============================================================
# 滚动训练
# ============================================================

def run_walk_forward(df, feature_columns):
    predictions = []

    folds = make_walk_forward(
        data=df,
        feature_columns=feature_columns,
        test_start=TEST_START,
        test_end=TEST_END,
        train_months=TRAIN_MONTHS,
        retrain_months=RETRAIN_MONTHS,
        min_train_samples=1000,
    )

    for fold in folds:
        train = df.loc[fold.train_idx]
        test = df.loc[fold.test_idx].copy()

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

        if y_train.nunique() < 2:
            raise ValueError(
                f"{fold.month}: only one training class"
            )

        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                max_iter=1000,
                random_state=42,
            ),
        )

        model.fit(X_train, y_train)

        probability = model.predict_proba(X_test)[:, 1]

        # 基线：每个训练窗口的历史上涨比例
        baseline = float(y_train.mean())

        result = test[
            [
                "open_time",
                "decision_time",
                "label_available_time",
                "reference_return",
                "target",
            ] + feature_columns
        ].copy()

        result["probability"] = probability
        result["baseline"] = baseline
        result["month"] = fold.month
        result["train_time"] = fold.train_time
        result["train_size"] = len(train)

        predictions.append(result)

        score = metrics(
            y_test.to_numpy(),
            probability,
        )

        print(
            f"{fold.month} "
            f"Train={len(train)} "
            f"Test={len(test)} "
            f"AUC={score['auc']:.4f} "
            f"LogLoss={score['logloss']:.4f} "
            f"Brier={score['brier']:.4f}"
        )

    if not predictions:
        raise ValueError("No valid prediction folds")

    return pd.concat(predictions, ignore_index=True)


# ============================================================
# 结果评价
# ============================================================

def evaluate_predictions(predictions):
    if predictions["open_time"].duplicated().any():
        raise ValueError("Duplicate prediction timestamps")

    y = predictions["target"].to_numpy(dtype=int)
    p = predictions["probability"].to_numpy(dtype=float)
    b = predictions["baseline"].to_numpy(dtype=float)

    model_score = metrics(y, p)
    baseline_score = metrics(y, b)

    print("\n=== WALK-FORWARD RESULTS ===")
    print("Samples:", len(y))
    print("Months:", predictions["month"].nunique())

    print("\nMODEL:")
    print(model_score)

    print("\nBASELINE:")
    print(baseline_score)

    print("\nIMPROVEMENT:")
    print(
        "LogLoss:",
        baseline_score["logloss"] - model_score["logloss"]
    )
    print(
        "Brier:",
        baseline_score["brier"] - model_score["brier"]
    )

    return {
        "model": model_score,
        "baseline": baseline_score,
    }


# ============================================================
# 入口
# ============================================================
from sklearn.linear_model import LogisticRegression

from alpha_lab.models.evaluator import evaluate_model
def main():
    df, feature_columns = prepare_dataset()

    result = evaluate_model(
        dataset=df,
        feature_columns=feature_columns,
        model_factory=lambda: LogisticRegression(
            max_iter=1000,
            random_state=42,
        ),
        test_start=TEST_START,
        test_end=TEST_END,
        train_months=TRAIN_MONTHS,
        retrain_months=RETRAIN_MONTHS,
        min_train_samples=1000,
        scale_features=True,
    )

    print("\n=== MODEL EVALUATION ===")
    print(result.metrics)

    print("\n=== MONTHLY RESULTS ===")
    print(result.fold_metrics.to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result.predictions.to_csv(OUTPUT, index=False)

    print("\nSaved:", OUTPUT)



if __name__ == "__main__":
    main()
