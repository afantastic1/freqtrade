
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from alpha_lab.models.evaluator import evaluate_model
from alpha_lab.evaluation.incremental_stability import (
    evaluate_stability,
)
# Pearson 衡量两个因子的线性相关程度。
# Spearman 衡量排序上的相似程度。
def factor_similarity(
    candidate: pd.Series,
    baseline: pd.Series,
) -> dict:
    """计算候选因子与基线因子的相关性。"""

    if not candidate.index.equals(baseline.index):
        raise ValueError("Factor indexes do not match")

    if not candidate.index.is_unique:
        raise ValueError("Factor index must be unique")

    data = pd.DataFrame({
        "candidate": candidate,
        "baseline": baseline,
    })

    data = data.replace(
        [np.inf, -np.inf],
        np.nan,
    ).dropna()

    if len(data) < 30:
        raise ValueError("Not enough valid observations")

    if (
        data["candidate"].nunique() < 2
        or data["baseline"].nunique() < 2
    ):
        return {
            "n": len(data),
            "pearson": float("nan"),
            "spearman": float("nan"),
        }

    return {
        "n": len(data),
        "pearson": float(
            data["candidate"].corr(
                data["baseline"],
                method="pearson",
            )
        ),
        "spearman": float(
            data["candidate"].corr(
                data["baseline"],
                method="spearman",
            )
        ),
    }


def evaluate_incremental(
    dataset: pd.DataFrame,
    candidate_column: str,
    baseline_columns: list[str],
    *,
    test_start: str,
    test_end: str,
    train_months: int = 12,
    retrain_months: int = 1,
    min_train_samples: int = 1000,
    bootstrap_samples: int = 1000,
    bootstrap_block_sizes: tuple[int, ...] = (24, 72, 168),
) -> dict:
    """
    在相同的有效样本和 Walk-Forward 条件下比较：

    A: 基线因子模型
    B: 候选因子模型
    C: 基线 + 候选因子模型

    同时计算候选因子与各基线因子的相似度。
    """
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")

    if not bootstrap_block_sizes or any(
        type(size) is not int or size < 1
        for size in bootstrap_block_sizes
    ):
        raise ValueError("Invalid bootstrap block sizes")
    
    if not baseline_columns:
        raise ValueError(
            "At least one baseline factor required"
        )

    if candidate_column in baseline_columns:
        raise ValueError(
            "Candidate cannot also be a baseline"
        )

    all_columns = baseline_columns + [candidate_column]

    if len(set(all_columns)) != len(all_columns):
        raise ValueError("Duplicate factor names")

    missing = set(all_columns) - set(dataset.columns)

    if missing:
        raise ValueError(
            f"Missing factor columns: {sorted(missing)}"
        )

    # 1. 统一有效样本
    common_valid = pd.Series(
        np.isfinite(
            dataset[all_columns].to_numpy(dtype=float)
        ).all(axis=1),
        index=dataset.index,
    )

    # 2. 因子相似度
    # 2. 计算候选因子与每个基线因子的相似度
    similarity = {}

    for baseline_name in baseline_columns:
        result = factor_similarity(
            candidate=dataset[candidate_column],
            baseline=dataset[baseline_name],
        )

        similarity[baseline_name] = result

        print(
            f"[Factor Similarity] "
            f"{candidate_column} vs {baseline_name}\n"
            f"  Samples:  {result['n']}\n"
            f"  Pearson:  {result['pearson']:.6f}\n"
            f"  Spearman: {result['spearman']:.6f}"
        )

    # 3. 三组模型
    groups = {
        "baseline": baseline_columns,
        "candidate": [candidate_column],
        "combined": all_columns,
    }

    results = {}

    for group_name, columns in groups.items():
        results[group_name] = evaluate_model(
            dataset=dataset,
            feature_columns=columns,
            model_factory=lambda: LogisticRegression(
                max_iter=1000,
                random_state=42,
            ),
            test_start=test_start,
            test_end=test_end,
            train_months=train_months,
            retrain_months=retrain_months,
            min_train_samples=min_train_samples,
            scale_features=True,
            sample_eligible=common_valid,
        )

    # 4. 严格检查三组模型的预测样本一致性
    anchor = results["baseline"].predictions

    for group_name in ("candidate", "combined"):
        current = results[group_name].predictions

        for column in (
            "open_time",
            "target",
            "train_time",
            "train_size",
        ):
            if not current[column].equals(anchor[column]):
                raise ValueError(
                    f"Prediction mismatch: "
                    f"{group_name}.{column}"
                )
    # 预测概率列需要与 ModelEvaluator 的实际输出一致
    probability_candidates = (
        "probability",
        "y_prob",
        "y_pred_proba",
        "predicted_probability",
        "prediction_proba",
        "prob",
    )

    probability_column = next(
        (
            name for name in probability_candidates
            if name in anchor.columns
            and all(
                name in results[group].predictions.columns
                for group in ("baseline", "combined")
            )
        ),
        None,
    )

    if probability_column is None:
        raise ValueError(
            "Cannot identify predicted probability column. "
            f"Available columns: {list(anchor.columns)}"
        )

    
    # 对三种时间块长度分别执行 Bootstrap。
    # 每次均使用同一批实际预测、标签与概率。
    stability_by_block = {}

    for block_size in bootstrap_block_sizes:
        report = evaluate_stability(
            timestamps=anchor["open_time"],
            y_true=anchor["target"],
            baseline_prob=results["baseline"].predictions[
                probability_column
            ],
            combined_prob=results["combined"].predictions[
                probability_column
            ],
            bootstrap_samples=bootstrap_samples,
            block_size=block_size,
            random_state=42,
        )

        stability_by_block[str(block_size)] = {
            "logloss": report["summary"]["logloss"],
            "brier": report["summary"]["brier"],
        }

    # 月度结果与块长度无关，只保留一次
    monthly_stability = report["monthly"]

    robust_positive = all(
        stability_by_block[str(size)][metric]["ci_95_lower"] > 0
        for size in bootstrap_block_sizes
        for metric in ("logloss", "brier")
    )

    # 5. 提取指标
    baseline_metrics = results["baseline"].metrics["model"]
    candidate_metrics = results["candidate"].metrics["model"]
    combined_metrics = results["combined"].metrics["model"]

    # 6. 返回完整的增量评价报告
    return {
        "factor_similarity": similarity,
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "combined": combined_metrics,
        "incremental": {
            "auc": (
                combined_metrics["auc"]
                - baseline_metrics["auc"]
            ),
            "logloss": (
                baseline_metrics["logloss"]
                - combined_metrics["logloss"]
            ),
            "brier": (
                baseline_metrics["brier"]
                - combined_metrics["brier"]
            ),
        },
        "stability": {
            "monthly": monthly_stability,
            "bootstrap_sensitivity": stability_by_block,
            "bootstrap_samples": bootstrap_samples,
            "robust_positive": robust_positive,
        },
        "n": len(anchor),
        
    }
