
import math


def build_research_feedback(
    candidate_name: str,
    incremental: dict,
) -> dict:
    """
    根据增量研究结果生成 Agent 可读取的反馈。

    注意：状态是研究流程标记，不是统计显著性判定。
    """
    similarity = incremental["factor_similarity"]
    improvement = incremental["incremental"]
    stability = incremental["stability"]

    correlations = [
        abs(float(v["spearman"]))
        for v in similarity.values()
        if math.isfinite(float(v["spearman"]))
    ]

    max_similarity = (
        max(correlations) if correlations else None
    )

    # 仅供研究去重的初始阈值
    high_redundancy = (
        max_similarity is not None
        and max_similarity >= 0.90
    )

    robust_positive = bool(
        stability["robust_positive"]
    )

    loss_gain = float(improvement["logloss"])
    brier_gain = float(improvement["brier"])

    positive_delta = loss_gain > 0 and brier_gain > 0

    tags = []

    if high_redundancy:
        tags.append("HIGH_REDUNDANCY")

    if not robust_positive:
        tags.append("WEAK_INCREMENTAL_EVIDENCE")

    if not positive_delta:
        tags.append("NO_CONSISTENT_POINT_IMPROVEMENT")

    if robust_positive and positive_delta:
        classification = "PROMISING"
        action = "VERIFY_ON_NEW_DATA"
    else:
        classification = "INCONCLUSIVE"

        if high_redundancy:
            action = "REFINE_OR_EXPLORE_NEW_HYPOTHESIS"
        else:
            action = "INVESTIGATE_INSTABILITY"

    return {
        "candidate": candidate_name,
        "classification": classification,
        "tags": tags,
        "evidence": {
            "max_abs_spearman": max_similarity,
            "delta_auc": float(improvement["auc"]),
            "delta_logloss": loss_gain,
            "delta_brier": brier_gain,
            "robust_positive": robust_positive,
        },
        "suggested_action": action,
        "policy_version": "initial_v1",
    }
