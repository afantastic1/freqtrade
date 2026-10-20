"""对已有 run 做**只读**的统计重评。

原则：
- 只读取 run 目录里的 artifact（summary.json + predictions_*.csv）；
- 不写入、不修改任何原始 append-only 记录；
- 所有计算都是确定性的（bootstrap 固定随机种子）；
- 输出既包含点估计，也包含有效样本量、MDE、区间与三分类判定。

重评相对原实现补齐的四件事：

1. ΔAUC 的不确定性（原实现只有点差，没有任何区间）；
2. 有效样本量（原实现把 n 当作独立样本数）；
3. MDE（原实现无法回答"这个设计能不能分辨出有意义的效应"）；
4. 多重检验校正（原实现逐 run 看 CI，不看族错误率）。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.stats.effective_samples import estimate_effective_samples
from alpha_lab.stats.metrics import brier_loss_per_sample, logloss_per_sample
from alpha_lab.stats.multiple_testing import adjust_pvalues, two_sided_binomial_p
from alpha_lab.stats.method_registry import REGISTRY_VERSION
from alpha_lab.stats.paired_bootstrap import (
    paired_block_bootstrap,
    resolve_block_size,
)
from alpha_lab.stats.power import (
    mde_from_gain_series,
    mde_from_standard_error,
    minimum_detectable_effect,
)
from alpha_lab.stats.verdict import (
    MetricAssessment,
    VerdictPolicy,
    classify_result,
    classify_formal_result,
    combine_verdicts,
    gate_formal_verdict,
)
from alpha_lab.version import STATISTICAL_METHOD_VERSION

PRIMARY_METRIC = "auc"
SECONDARY_METRICS = ("logloss", "brier")
DEFAULT_ROBUSTNESS_BLOCKS = (24, 72, 168)

# Fisher z 变换的 RankIC 标准误：se = 1/sqrt(n_eff - 3)
FISHER_MIN_N_EFF = 10


def load_run_summary(run_dir: Path) -> dict:
    path = Path(run_dir) / "summary.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing summary.json: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_incremental_predictions(run_dir: Path) -> pd.DataFrame:
    """读取并严格对齐基线与组合模型的预测。"""
    run_dir = Path(run_dir)
    base = pd.read_csv(run_dir / "predictions_baseline.csv")
    comb = pd.read_csv(run_dir / "predictions_combined.csv")

    required = {"open_time", "target", "probability"}
    for name, frame in (("baseline", base), ("combined", comb)):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} predictions missing {sorted(missing)}")

    if len(base) != len(comb):
        raise ValueError("baseline/combined prediction row counts differ")
    if not (base["open_time"].to_numpy() == comb["open_time"].to_numpy()).all():
        raise ValueError("baseline/combined prediction timelines differ")

    return pd.DataFrame({
        "open_time": pd.to_datetime(base["open_time"], utc=True),
        "target": base["target"].to_numpy(dtype=float),
        "p_base": base["probability"].to_numpy(dtype=float),
        "p_comb": comb["probability"].to_numpy(dtype=float),
    })


def candidate_factor_key(summary: dict) -> str | None:
    """从 factor_evaluation 里找出候选因子自己的键（排除基线别名）。"""
    evidence = summary.get("evidence_report") or {}
    context = evidence.get("evaluation_context") or {}
    baseline = (context.get("baseline") or {}) if context else {}
    factors = baseline.get("factors") or (summary.get("baseline") or {}).get(
        "factors", []
    )

    baseline_keys = set()
    for spec in factors:
        alias = spec.get("alias")
        if alias:
            baseline_keys.add(alias)
            continue
        params = spec.get("params") or {}
        suffix = "_".join(f"{k}_{v}" for k, v in sorted(params.items()))
        baseline_keys.add(f"{spec.get('name')}_{suffix}" if suffix else spec.get("name"))

    evaluation = (
        (evidence.get("factor_evaluation") if evidence else None)
        or summary.get("factor_evaluation")
        or {}
    )

    candidates = [key for key in evaluation if key not in baseline_keys]
    if len(candidates) == 1:
        return candidates[0]

    # 回退：直接从 candidate 的 name + parameters 构造期望键。
    # 部分回填 run 的 evidence_report 与 summary["baseline"] **同时**缺失，
    # 此时无法用"排除基线键"定位候选因子（全部键都会被当成候选）。
    # 构造式与 FactorSpec.output_name 完全一致，且只使用运行前就固定的
    # candidate.json 字段，不依赖任何观测结果，因此不会引入事后拟合。
    candidate = summary.get("candidate") or {}
    name = candidate.get("name")
    if name:
        params = candidate.get("parameters") or {}
        suffix = "_".join(
            f"{key}_{value}" for key, value in sorted(params.items())
        )
        expected = f"{name}_{suffix}" if suffix else name
        if expected in evaluation:
            return expected

    return None


def monthly_gain_stability(
    times: pd.Series, gains: np.ndarray
) -> dict:
    """按月统计配对增益，给出方向稳定性。"""
    stamps = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    frame = pd.DataFrame({
        "month": stamps.strftime("%Y-%m"),
        "gain": np.asarray(gains, dtype=float),
    })
    grouped = frame.groupby("month")["gain"]
    monthly = grouped.mean()

    if monthly.empty:
        return {
            "months": 0,
            "positive_months": 0,
            "positive_ratio": float("nan"),
            "monthly": [],
        }

    positive = int((monthly > 0.0).sum())
    months = int(monthly.shape[0])

    # 月度符号的二项检验（H0: p=0.5）。用于量化"不稳定"而不是只看阈值。
    sign_test_p = two_sided_binomial_p(positive, months)

    return {
        "months": months,
        "positive_months": positive,
        "positive_ratio": float(positive / months),
        "monthly_sign_test_p": sign_test_p,
        "min_monthly": float(monthly.min()),
        "max_monthly": float(monthly.max()),
        "monthly": [
            {"month": str(month), "gain": float(value)}
            for month, value in monthly.items()
        ],
    }


def rank_ic_sign_evidence(
    rank_ic: float | None,
    negative_ratio: float | None,
    valid_months: int | None,
    *,
    n_eff: float | None = None,
) -> dict:
    """用逐月 RankIC 的符号检验评估机制方向。

    这是一个**保守且只用 artifact** 的方向检验：
    逐月符号近似独立（同一因子同一标签，月与月重叠很小），
    因此对 36 个月做二项检验比把 n 根 K 线当独立样本更诚实。
    """
    result = {
        "rank_ic": rank_ic,
        "negative_month_ratio": negative_ratio,
        "valid_months": valid_months,
        "fisher_se": None,
        "mde_rank_ic": None,
        "sign_test_p": None,
        "observed_sign": None,
        "monthly_observed_sign": None,
        "mde_assumption": (
            "Fisher approximation with statistic-specific n_eff" if n_eff is not None
            else "unavailable: saved artifacts lack the RankIC time series"
        ),
    }

    if rank_ic is not None and np.isfinite(rank_ic):
        result["observed_sign"] = int(np.sign(rank_ic))

    if n_eff and np.isfinite(n_eff) and n_eff > FISHER_MIN_N_EFF:
        se = 1.0 / float(np.sqrt(n_eff - 3.0))
        result["fisher_se"] = se
        result["mde_rank_ic"] = minimum_detectable_effect(se)

    if (
        negative_ratio is not None
        and valid_months is not None
        and valid_months > 0
    ):
        k = int(round(negative_ratio * valid_months))
        k = max(0, min(k, valid_months))
        result["sign_test_p"] = two_sided_binomial_p(k, valid_months)
        result["negative_months"] = k
        positive = valid_months - k
        if negative_ratio > 0.5:
            result["monthly_observed_sign"] = -1
        elif positive / valid_months > 0.5:
            result["monthly_observed_sign"] = 1

    return result


def audit_run(
    run_dir: Path,
    *,
    n_boot: int = 2000,
    robustness_boot: int | None = None,
    random_state: int = 42,
    preregistered_sign: int | None = None,
    policy: VerdictPolicy | None = None,
    robustness_blocks: tuple[int, ...] = DEFAULT_ROBUSTNESS_BLOCKS,
) -> dict:
    """重评单个 run。返回纯 dict，便于持久化与比对。"""
    run_dir = Path(run_dir)
    robustness_boot = int(robustness_boot or max(1, n_boot // 3))
    summary = load_run_summary(run_dir)
    preds = load_incremental_predictions(run_dir)

    evidence = summary.get("evidence_report") or {}
    context = evidence.get("evaluation_context") or {}
    data_block = context.get("data") or {}

    horizon = int(
        data_block.get("label_horizon")
        or summary.get("horizon")
        or (summary.get("candidate") or {}).get("prediction_horizon")
        or 1
    )
    timeframe = data_block.get("timeframe") or summary.get("timeframe")
    candidate = summary.get("candidate") or {}

    y = preds["target"].to_numpy(dtype=float)
    p_base = preds["p_base"].to_numpy(dtype=float)
    p_comb = preds["p_comb"].to_numpy(dtype=float)
    n = int(y.shape[0])

    block_size = resolve_block_size(n, horizon)

    bootstrap = paired_block_bootstrap(
        y,
        p_base,
        p_comb,
        block_size=block_size,
        n_boot=n_boot,
        random_state=random_state,
    )

    robustness: dict = {}
    for requested in robustness_blocks:
        if requested < horizon:
            # 原实现固定使用 24/72/168。当 horizon > block 时，
            # 块内无法容纳一个完整标签窗口，该块长在方法上无效，
            # 记录为 skipped 而不是静默使用。
            robustness[str(requested)] = {
                "skipped": "block_size_below_label_horizon"
            }
            continue
        if requested > n:
            continue
        size = resolve_block_size(n, horizon, requested=requested)
        robustness[str(requested)] = {
            name: bootstrap_result.as_dict()
            for name, bootstrap_result in paired_block_bootstrap(
                y,
                p_base,
                p_comb,
                block_size=size,
                n_boot=robustness_boot,
                random_state=random_state,
                metrics=(PRIMARY_METRIC, "logloss"),
            ).items()
        }

    gains_logloss = logloss_per_sample(y, p_base) - logloss_per_sample(y, p_comb)

    ess = estimate_effective_samples(gains_logloss, horizon=horizon)

    stability = monthly_gain_stability(preds["open_time"], gains_logloss)

    # 方向一致性：预注册符号 vs 观测到的候选因子 RankIC 符号
    factor_key = candidate_factor_key(summary)
    factor_block = (
        ((evidence.get("factor_evaluation") or {}) if evidence else {})
        .get(factor_key, {})
        if factor_key
        else {}
    ) or ((summary.get("factor_evaluation") or {}).get(factor_key, {}) if factor_key else {})

    factor_stability = factor_block.get("stability") or {}
    rank_ic_evidence = rank_ic_sign_evidence(
        (factor_block.get("overall") or {}).get("rank_ic"),
        factor_stability.get("negative_rank_ic_ratio"),
        factor_stability.get("valid_months"),
    )

    direction_consistent = None
    if preregistered_sign is not None and rank_ic_evidence["observed_sign"] is not None:
        direction_consistent = (
            int(rank_ic_evidence["observed_sign"]) == int(preregistered_sign)
        )

    # --- 构造各指标 assessment ---
    #
    # MDE 的口径（关键）：
    # 主口径用 **bootstrap 标准误**，因为 block bootstrap 的块长已被强制为
    # >= horizon，所以它已经在经验上吸收了标签重叠带来的依赖。
    # 设计口径（sd/sqrt(n/horizon)）作为**更保守的**交叉校验单独报告：
    # 它假设同一标签窗口内的所有行完全相关，通常高估 SE。
    policy = policy or VerdictPolicy()
    assessments: dict[str, MetricAssessment] = {}
    mde_detail: dict[str, dict] = {}

    for name in (PRIMARY_METRIC,) + SECONDARY_METRICS:
        result = bootstrap[name]
        mde_bootstrap = minimum_detectable_effect(result.boot_sd)

        detail = {"mde_bootstrap": float(mde_bootstrap)}
        if name == "logloss":
            gains = gains_logloss
        elif name == "brier":
            gains = brier_loss_per_sample(
                y, preds["p_base"].to_numpy()
            ) - brier_loss_per_sample(y, preds["p_comb"].to_numpy())
        else:
            gains = None

        if gains is not None:
            analytic = mde_from_gain_series(gains, n_eff=ess.n_eff)
            detail["mde_design_conservative"] = analytic["mde"]
            detail["sample_sd"] = analytic["sample_sd"]

        mde_detail[name] = detail

        assessments[name] = MetricAssessment(
            metric=name,
            point=result.point,
            ci_lower=result.ci_95_lower,
            ci_upper=result.ci_95_upper,
            mde=float(mde_bootstrap),
            practical_threshold=policy.threshold_for(name),
            p_two_sided=result.p_two_sided,
        )

    # 冗余
    similarity = (
        (evidence.get("incremental_evaluation") or {}).get("factor_similarity")
        if evidence
        else None
    ) or ((summary.get("incremental_evaluation") or {}).get("factor_similarity") or {})

    spearmans = [
        float(abs(entry["spearman"]))
        for entry in similarity.values()
        if isinstance(entry, dict) and entry.get("spearman") is not None
    ]
    max_abs_spearman = max(spearmans) if spearmans else None

    return {
        "run_id": summary.get("run_id", run_dir.name),
        "method_version": STATISTICAL_METHOD_VERSION,
        "statistical_registry_version": REGISTRY_VERSION,
        "run_dir": str(run_dir),
        "candidate": candidate.get("name"),
        "timeframe": timeframe,
        "horizon": horizon,
        "n": n,
        "n_eff": {
            "design": ess.n_eff_design,
            "autocorr": ess.n_eff_autocorr,
            "conservative": ess.conservative_n_eff,
            "variance_inflation": ess.variance_inflation,
            "max_lag": ess.max_lag,
        },
        "block_size": block_size,
        "bootstrap_samples": n_boot,
        "metrics": {
            name: {**assessment.as_dict(), **mde_detail.get(name, {})}
            for name, assessment in assessments.items()
        },
        "robustness_blocks": robustness,
        "stability": stability,
        "redundancy": {
            "max_abs_spearman": max_abs_spearman,
            "per_factor": {
                key: (value.get("spearman") if isinstance(value, dict) else None)
                for key, value in similarity.items()
            },
        },
        "rank_ic_evidence": rank_ic_evidence,
        "preregistered_sign": preregistered_sign,
        "direction_consistent": direction_consistent,
        "stored": _stored_point_estimates(summary),
    }


def _stored_point_estimates(summary: dict) -> dict:
    """原 append-only 记录里的点估计，用于交叉校验。"""
    evidence = summary.get("evidence_report") or {}
    incremental = (
        (evidence.get("incremental_evaluation") if evidence else None)
        or summary.get("incremental_evaluation")
        or {}
    )
    stored = incremental.get("incremental") or {}
    stability = incremental.get("stability") or {}
    return {
        "source": "evidence_report" if evidence else "summary",
        "incremental": {
            key: (float(value) if value is not None else None)
            for key, value in stored.items()
        },
        "robust_positive": stability.get("robust_positive"),
        "bootstrap_sensitivity": stability.get("bootstrap_sensitivity"),
    }


def audit_campaign(
    run_dirs: list[Path],
    *,
    preregistered_signs: dict[str, int] | None = None,
    n_boot: int = 2000,
    robustness_boot: int | None = None,
    random_state: int = 42,
    policy: VerdictPolicy | None = None,
) -> dict:
    """对一组 run 做重评，并施加多重检验校正与终判。"""
    policy = policy or VerdictPolicy()
    preregistered_signs = preregistered_signs or {}
    robustness_boot = int(robustness_boot or max(1, n_boot // 3))

    audits = []
    for run_dir in run_dirs:
        run_dir = Path(run_dir)
        try:
            summary = load_run_summary(run_dir)
        except FileNotFoundError:
            continue

        run_id = summary.get("run_id", run_dir.name)
        audit = audit_run(
            run_dir,
            n_boot=n_boot,
            robustness_boot=robustness_boot,
            random_state=random_state,
            preregistered_sign=preregistered_signs.get(run_id),
            policy=policy,
        )
        audits.append(audit)

    # --- 多重检验：族 = 全部被重评的 run，指标 = 主指标 ---
    raw_p = [
        audit["metrics"][PRIMARY_METRIC]["p_two_sided"] for audit in audits
    ]

    finite = np.isfinite(np.asarray(raw_p, dtype=float))
    adjusted = adjust_pvalues(np.asarray(raw_p, dtype=float)[finite])
    adjusted_iter = iter(adjusted["benjamini_hochberg"])
    holm_iter = iter(adjusted["holm_bonferroni"])

    for audit, ok in zip(audits, finite):
        if ok:
            audit["metrics"][PRIMARY_METRIC]["adjusted_p"] = float(
                next(adjusted_iter)
            )
            audit["metrics"][PRIMARY_METRIC]["adjusted_p_holm"] = float(
                next(holm_iter)
            )
        else:
            audit["metrics"][PRIMARY_METRIC]["adjusted_p"] = None
            audit["metrics"][PRIMARY_METRIC]["adjusted_p_holm"] = None

    # --- 机制方向轴的多重检验：对逐月符号检验 p 值做 BH 校正 ---
    # 机制检验的族与增量检验相同（同一批 11 个实验），因此同样需要校正。
    mechanism_indices = [
        i
        for i, audit in enumerate(audits)
        if audit["rank_ic_evidence"].get("sign_test_p") is not None
    ]
    if mechanism_indices:
        mechanism_p = np.asarray(
            [
                audits[i]["rank_ic_evidence"]["sign_test_p"]
                for i in mechanism_indices
            ],
            dtype=float,
        )
        mechanism_adjusted = adjust_pvalues(mechanism_p)
        for position, index in enumerate(mechanism_indices):
            audits[index]["rank_ic_evidence"]["sign_test_p_adjusted"] = float(
                mechanism_adjusted["benjamini_hochberg"][position]
            )

    # --- 终判 ---
    for audit in audits:
        assessments = {
            name: MetricAssessment(
                metric=item["metric"],
                point=item["point"],
                ci_lower=item["ci_95_lower"],
                ci_upper=item["ci_95_upper"],
                mde=item["mde"],
                practical_threshold=item["practical_threshold"],
                adjusted_p=item.get("adjusted_p"),
                p_two_sided=item.get("p_two_sided"),
            )
            for name, item in audit["metrics"].items()
        }

        verdict = classify_formal_result(
            assessments,
            primary_metric=PRIMARY_METRIC,
            method_id=STATISTICAL_METHOD_VERSION,
            stability_ratio=audit["stability"].get("positive_ratio"),
            max_abs_spearman=audit["redundancy"]["max_abs_spearman"],
            policy=policy,
        )
        audit["verdict"] = verdict.as_dict()

        audit["mechanism_verdict"] = gate_formal_verdict(
            _mechanism_verdict(
                audit["rank_ic_evidence"], audit["direction_consistent"], policy
            ),
            method_id=STATISTICAL_METHOD_VERSION,
        )
        audit["overall_verdict"] = gate_formal_verdict(
            combine_verdicts(audit["verdict"], audit["mechanism_verdict"]),
            method_id=STATISTICAL_METHOD_VERSION,
        )

    # --- 实际意义阈值敏感性：判定不应由阈值的第三位小数决定 ---
    sensitivity = {}
    base_thresholds = dict(policy.practical_thresholds)
    for scale in (0.5, 0.8, 1.0, 1.5, 2.0):
        alt_policy = VerdictPolicy(
            alpha=policy.alpha,
            power=policy.power,
            redundancy_threshold=policy.redundancy_threshold,
            min_stability_ratio=policy.min_stability_ratio,
            require_p_adjusted=policy.require_p_adjusted,
            practical_thresholds={
                name: value * scale
                for name, value in base_thresholds.items()
            },
        )
        labels = {}
        for audit in audits:
            assessments = {
                name: MetricAssessment(
                    metric=item["metric"],
                    point=item["point"],
                    ci_lower=item["ci_95_lower"],
                    ci_upper=item["ci_95_upper"],
                    mde=item["mde"],
                    practical_threshold=alt_policy.threshold_for(name),
                    adjusted_p=item.get("adjusted_p"),
                    p_two_sided=item.get("p_two_sided"),
                )
                for name, item in audit["metrics"].items()
            }
            incremental = classify_formal_result(
                assessments,
                primary_metric=PRIMARY_METRIC,
                method_id=STATISTICAL_METHOD_VERSION,
                stability_ratio=audit["stability"].get("positive_ratio"),
                max_abs_spearman=audit["redundancy"]["max_abs_spearman"],
                policy=alt_policy,
            )
            overall = combine_verdicts(incremental, audit["mechanism_verdict"])
            labels[audit["run_id"]] = {
                "incremental": incremental.label,
                "overall": overall["label"],
            }
        sensitivity[f"x{scale}"] = {
            "auc_threshold": base_thresholds["auc"] * scale,
            "labels": labels,
        }

    return {
        "policy": {
            "alpha": policy.alpha,
            "power": policy.power,
            "redundancy_threshold": policy.redundancy_threshold,
            "min_stability_ratio": policy.min_stability_ratio,
            "practical_thresholds": base_thresholds,
        },
        "n_boot": n_boot,
        "robustness_boot": robustness_boot,
        "random_state": random_state,
        "multiple_testing": {
            "family_size": adjusted["family_size"],
            "metric": PRIMARY_METRIC,
        },
        "threshold_sensitivity": sensitivity,
        "runs": audits,
    }


def _mechanism_verdict(
    rank_ic_evidence: dict,
    direction_consistent: bool | None,
    policy: VerdictPolicy,
) -> dict:
    """机制方向的三分类：与增量判定完全分开。

    使用**BH 校正后**的逐月符号检验 p 值（族 = 同时重评的实验数），
    因此不会因为只看单个候选而夸大方向证据。
    """
    if direction_consistent is None:
        return {"label": "INCONCLUSIVE", "reasons": ["NO_PREREGISTERED_DIRECTION"]}

    raw_p = rank_ic_evidence.get("sign_test_p")
    adjusted_p = rank_ic_evidence.get("sign_test_p_adjusted", raw_p)
    rank_ic = rank_ic_evidence.get("rank_ic")
    mde = rank_ic_evidence.get("mde_rank_ic")

    significant = adjusted_p is not None and adjusted_p < policy.alpha
    monthly_sign = rank_ic_evidence.get("monthly_observed_sign")
    overall_sign = rank_ic_evidence.get("observed_sign")

    if monthly_sign in (None, 0) or not significant:
        return {
            "label": "INCONCLUSIVE",
            "reasons": ["DIRECTION_NOT_RESOLVED"],
            "rank_ic": rank_ic,
            "sign_test_p": raw_p,
            "sign_test_p_adjusted": adjusted_p,
            "mde_rank_ic": mde,
        }
    if overall_sign not in (None, 0) and monthly_sign != overall_sign:
        return {
            "label": "INCONCLUSIVE",
            "reasons": ["OVERALL_MONTHLY_DIRECTION_CONFLICT"],
            "rank_ic": rank_ic,
            "sign_test_p": raw_p,
            "sign_test_p_adjusted": adjusted_p,
            "mde_rank_ic": mde,
        }

    if direction_consistent:
        return {
            "label": "SUPPORTED",
            "reasons": ["PRE_REGISTERED_DIRECTION_CONFIRMED"],
            "rank_ic": rank_ic,
            "sign_test_p": raw_p,
            "sign_test_p_adjusted": adjusted_p,
            "mde_rank_ic": mde,
        }

    if not direction_consistent:
        return {
            "label": "REJECTED",
            "reasons": ["PRE_REGISTERED_DIRECTION_CONFIDENTLY_CONTRADICTED"],
            "rank_ic": rank_ic,
            "sign_test_p": raw_p,
            "sign_test_p_adjusted": adjusted_p,
            "mde_rank_ic": mde,
        }

    return {
        "label": "INCONCLUSIVE",
        "reasons": ["DIRECTION_NOT_RESOLVED"],
        "rank_ic": rank_ic,
        "sign_test_p": raw_p,
        "sign_test_p_adjusted": adjusted_p,
        "mde_rank_ic": mde,
    }
