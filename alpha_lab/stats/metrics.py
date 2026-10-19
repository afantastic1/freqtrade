"""逐样本损失函数与 AUC。

与 alpha_lab.evaluation.incremental_stability 保持数值一致，
但额外提供 AUC（原实现完全没有 AUC 的不确定性度量）。
"""

import numpy as np

EPS = 1e-15


def logloss_per_sample(y, p) -> np.ndarray:
    """逐样本 log loss。p 会被裁剪，避免 log(0)。"""
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), EPS, 1.0 - EPS)
    return -(y * np.log(p) + (1.0 - y) * np.log1p(-p))


def brier_loss_per_sample(y, p) -> np.ndarray:
    """逐样本 Brier loss。"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return (p - y) ** 2


def fast_auc(y, p) -> float:
    """基于秩的二分类 AUC，带并列值平均秩。

    与 sklearn.metrics.roc_auc_score 数值一致（并列值使用平均秩），
    但快得多，便于在 bootstrap 里重复调用。
    样本只有单一类别时返回 nan。
    """
    y = np.asarray(y, dtype=float).ravel()
    p = np.asarray(p, dtype=float).ravel()

    if y.shape != p.shape:
        raise ValueError("y and p must have the same shape")

    n = y.shape[0]
    n_pos = int((y == 1).sum())
    n_neg = n - n_pos

    if n == 0 or n_pos == 0 or n_neg == 0:
        return float("nan")

    if not np.isfinite(p).all():
        raise ValueError("Non-finite probabilities")

    ranks = _average_ranks(p)
    rank_sum_pos = ranks[y == 1].sum()

    return float((rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _average_ranks(p: np.ndarray) -> np.ndarray:
    """1-based 平均秩，完全向量化（bootstrap 内层循环的性能关键）。"""
    n = p.shape[0]
    order = np.argsort(p, kind="mergesort")
    sorted_p = p[order]

    is_new_group = np.empty(n, dtype=bool)
    is_new_group[0] = True
    np.not_equal(sorted_p[1:], sorted_p[:-1], out=is_new_group[1:])

    group_ids = np.cumsum(is_new_group) - 1
    starts = np.flatnonzero(is_new_group)
    counts = np.diff(np.append(starts, n)).astype(float)

    # 每组 1-based 平均秩： start + (count - 1)/2 + 1
    group_average = starts.astype(float) + (counts - 1.0) / 2.0 + 1.0

    ranks = np.empty(n, dtype=float)
    ranks[order] = group_average[group_ids]
    return ranks
