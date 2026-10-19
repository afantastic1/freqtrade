"""统计方法自检（第四轮方法学审计，P0）。

本模块回答上一轮审计留下的四个方法学问题，全部是**确定性**计算，
且**不写入、不修改**任何 append-only 记录。

## Q1 主指标是否存在**事后选择**？

审计把 `ΔAUC` 作为主指标（因为它有功效，而 `Δlogloss`/`ΔBrier` 的
逐样本噪声约为其均值的 100 倍）。这一选择本身是**在看到 logloss/brier
的噪声之后**做出的，因此属于事后选择，必须被显式检验：
把 11 个实验分别在 `auc` / `logloss` / `brier` 三种主指标下重新判定，
看结论是否"只在对我们有利的那一个指标下成立"。

## Q2 BH 检验的**族**是否定义清晰？

族定义是一个自由度。本模块在同一批原始 p 值上，用**多个**族定义
重新做 BH 校正，报告结论是否随族定义变化。

## Q3 block bootstrap 是否覆盖**标签重叠**依赖？

两个安慰剂检验：

1. `placebo_from_predictions`：对每个已有 run 的**已保存预测**做
   "标签循环平移"（circular shift）。平移保持标签自身的自相关与
   预测自身的结构，只破坏"预测-标签"的对应关系，因此真实增量应为 0。
   然后看 ΔAUC 的 95% 区间有多频繁地错误排除 0（理想 = 5%），
   并对比 `block=1` 与 `block=horizon`。这是对**bootstrap 层**的直接检验，
   且几乎零额外拟合成本。
2. `placebo_walk_forward`：在 1h 数据上把标签循环平移后**重新做
   walk-forward 训练**，测量含"拟合过拟合偏差"在内的端到端假阳性率。

## Q4 `n_eff` 与 `MDE` 的口径是否一致？

审计用**测试段** n 与 **bootstrap** 标准误；预注册用**全窗口** n 与
**解析** n_eff。本模块并列两套口径并给出统一规则。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.stats.audit import (
    load_incremental_predictions,
    load_run_summary,
)
from alpha_lab.stats.metrics import fast_auc
from alpha_lab.stats.multiple_testing import adjust_pvalues
from alpha_lab.stats.paired_bootstrap import (
    paired_block_bootstrap,
    resolve_block_size,
)
from alpha_lab.stats.verdict import (
    MetricAssessment,
    VerdictPolicy,
    classify_result,
    combine_verdicts,
)

METRICS = ("auc", "logloss", "brier")
# 默认只比较两个关键块长：block=1（方法上无效）与 block=horizon（主口径）。
# 每增加一个块长都会成比例增加安慰剂成本（bootstrap 以 n log n 排序为瓶颈）。
PLACEBO_BLOCKS = (("one", 1), ("horizon", None))
PLACEBO_BLOCKS_EXTENDED = (("one", 1), ("horizon", None), ("3x_horizon", 3))


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------
def assessments_from_audit(run: dict, policy: VerdictPolicy) -> dict:
    """从重评 dict 还原 MetricAssessment（阈值取自给定 policy）。"""
    return {
        name: MetricAssessment(
            metric=item["metric"],
            point=item["point"],
            ci_lower=item["ci_95_lower"],
            ci_upper=item["ci_95_upper"],
            mde=item["mde"],
            practical_threshold=policy.threshold_for(name),
            adjusted_p=item.get("adjusted_p"),
            p_two_sided=item.get("p_two_sided"),
        )
        for name, item in run["metrics"].items()
    }


def _incremental_label(run: dict, policy: VerdictPolicy, metric: str):
    verdict = classify_result(
        assessments_from_audit(run, policy),
        primary_metric=metric,
        stability_ratio=run["stability"].get("positive_ratio"),
        max_abs_spearman=run["redundancy"]["max_abs_spearman"],
        policy=policy,
    )
    return verdict, combine_verdicts(verdict, run["mechanism_verdict"])


def _policy_with(policy: VerdictPolicy, *, strict: bool) -> VerdictPolicy:
    return VerdictPolicy(
        alpha=policy.alpha,
        power=policy.power,
        redundancy_threshold=policy.redundancy_threshold,
        min_stability_ratio=policy.min_stability_ratio,
        practical_thresholds=dict(policy.practical_thresholds),
        require_p_adjusted=policy.require_p_adjusted,
        require_concordant_metrics=strict,
    )


# ---------------------------------------------------------------------------
# Q1 主指标的事后选择
# ---------------------------------------------------------------------------
def primary_metric_sensitivity(
    runs: list[dict], policy: VerdictPolicy | None = None
) -> dict:
    policy = policy or VerdictPolicy()

    recorded = {
        run["run_id"]: {
            "incremental": run["verdict"]["label"],
            "overall": run["overall_verdict"]["label"],
        }
        for run in runs
    }

    variants: dict[str, dict] = {}
    for metric in METRICS:
        for strict in (False, True):
            alt = _policy_with(policy, strict=strict)
            labels = {}
            for run in runs:
                incremental, overall = _incremental_label(run, alt, metric)
                labels[run["run_id"]] = {
                    "incremental": incremental.label,
                    "overall": overall["label"],
                }
            flips = {
                run_id: {"recorded": recorded[run_id], "variant": label}
                for run_id, label in labels.items()
                if label != recorded[run_id]
            }
            variants[f"{metric}{'_strict' if strict else ''}"] = {
                "labels": labels,
                "n_flips_vs_recorded": len(flips),
                "flips_vs_recorded": flips,
            }

    structural = {}
    for run in runs:
        row = {}
        for metric in METRICS:
            item = run["metrics"][metric]
            row[f"{metric}_mde_over_threshold"] = (
                item["mde"] / item["practical_threshold"]
                if item["practical_threshold"]
                else None
            )
        structural[run["run_id"]] = row

    # 关键判据：是否存在"只有某个主指标才支持"的结论
    self_serving = []
    for run_id, base in recorded.items():
        for name, variant in variants.items():
            label = variant["labels"][run_id]
            if label["overall"] == "SUPPORTED" and base["overall"] != "SUPPORTED":
                self_serving.append(
                    {"run_id": run_id, "variant": name, "overall": label["overall"]}
                )

    return {
        "recorded_primary_metric": "auc",
        "recorded": recorded,
        "variants": variants,
        "structural_mde_over_threshold": structural,
        "self_serving_flips": self_serving,
        "any_self_serving_flip": bool(self_serving),
    }


# ---------------------------------------------------------------------------
# Q2 族定义
# ---------------------------------------------------------------------------
def family_sensitivity(
    runs: list[dict],
    variants: dict[str, list[str]],
    policy: VerdictPolicy | None = None,
) -> dict:
    """在不同族定义下重做 BH 校正并重判（同一批原始 p 值）。"""
    policy = policy or VerdictPolicy()
    by_id = {run["run_id"]: run for run in runs}

    out: dict[str, dict] = {}
    for name, run_ids in variants.items():
        ids = [run_id for run_id in run_ids if run_id in by_id]
        if not ids:
            out[name] = {"family_size": 0, "labels": {}}
            continue
        raw = np.asarray(
            [by_id[run_id]["metrics"]["auc"]["p_two_sided"] for run_id in ids],
            dtype=float,
        )
        adjusted = adjust_pvalues(raw)["benjamini_hochberg"]

        labels = {}
        for position, run_id in enumerate(ids):
            run = by_id[run_id]
            assessments = assessments_from_audit(run, policy)
            assessments["auc"] = replace(
                assessments["auc"], adjusted_p=float(adjusted[position])
            )
            incremental = classify_result(
                assessments,
                primary_metric="auc",
                stability_ratio=run["stability"].get("positive_ratio"),
                max_abs_spearman=run["redundancy"]["max_abs_spearman"],
                policy=policy,
            )
            labels[run_id] = {
                "adjusted_p": float(adjusted[position]),
                "incremental": incremental.label,
            }
        out[name] = {"family_size": len(ids), "labels": labels}

    return out


# ---------------------------------------------------------------------------
# Q4 n_eff / MDE 口径一致性
# ---------------------------------------------------------------------------
# 全窗口 K 线数（研究区间 [2022-01-01, 2025-01-01) 实测）。
# 审计用**测试段** n；预注册的功效表用**全窗口** n。两者比例 ≈ 1.5，
# 因此预注册的规划 MDE 乐观了约 sqrt(1.5) ≈ 1.22 倍。
FULL_WINDOW_N = {"5m": 315632, "15m": 105216, "1h": 26304, "4h": 6576}


def neff_mde_consistency(
    runs: list[dict],
    full_window_n: dict[str, int] | None = None,
) -> dict:
    """并列审计口径（测试段 n）与预注册口径（全窗口 n + 解析 n_eff）。"""
    full_window_n = full_window_n or FULL_WINDOW_N
    rows = []
    for run in runs:
        n = run["n"]
        horizon = run["horizon"]
        n_eff = run["n_eff"]
        design = float(n_eff["design"])
        autocorr = float(n_eff["autocorr"])
        vif = float(n_eff["variance_inflation"])
        boot_mde = float(run["metrics"]["auc"]["mde"])

        n_full = full_window_n.get(run["timeframe"])
        scaling = (
            float(np.sqrt(n_full / n)) if n_full and n else None
        )

        rows.append({
            "run_id": run["run_id"],
            "timeframe": run["timeframe"],
            "horizon": horizon,
            "n_eval": n,
            "n_full_window": n_full,
            "n_eff_design_n_over_h": design,
            "n_eff_autocorr_n_over_vif": autocorr,
            "variance_inflation": vif,
            "max_lag": n_eff["max_lag"],
            "mde_auc_bootstrap": boot_mde,
            "mde_scaling_if_full_window_planning": scaling,
            "n_eff_definition_disagreement": (
                abs(design - autocorr) / max(design, autocorr)
            ),
        })

    return {
        "canonical_rule": {
            "n_eff": "n_eval / max(horizon, VIF)",
            "n_eval": "evaluation segment rows (test segment), not full window",
            "vif": "Newey-West of the per-sample paired gain, max_lag = feature update period",
            "mde_primary": "2.802 * bootstrap_sd (empirical, block = max(horizon, feature period))",
            "mde_secondary": "2.802 * sample_sd / sqrt(n_eff) (analytic design cross-check)",
        },
        "rows": rows,
        "max_n_eff_definition_disagreement": max(
            (row["n_eff_definition_disagreement"] for row in rows), default=None
        ),
    }


# ---------------------------------------------------------------------------
# Q3a 安慰剂：对已保存预测做标签循环平移
# ---------------------------------------------------------------------------
def _placebo_blocks_for_run(
    y: np.ndarray,
    p_base: np.ndarray,
    p_comb: np.ndarray,
    *,
    horizon: int,
    n_shift: int,
    n_boot: int,
    random_state: int,
    blocks: tuple = PLACEBO_BLOCKS,
    max_shifts_for_blocks: int | None = None,
) -> dict:
    n = y.shape[0]
    rng = np.random.default_rng(random_state)
    min_lag = max(horizon * 3, 12)
    high = n - min_lag
    if high <= min_lag:
        raise ValueError("series too short for placebo shifts")
    count = min(n_shift, high - min_lag)
    shifts = rng.choice(np.arange(min_lag, high), size=count, replace=False)

    out = {}
    for name, factor in blocks:
        size = 1 if factor is None else int(factor) * horizon
        size = int(min(max(size, 1), n))
        fpr_ci = 0
        fpr_p = 0
        covered = 0
        deltas = []
        for k in shifts:
            shifted = np.roll(y, int(k))
            result = paired_block_bootstrap(
                shifted,
                p_base,
                p_comb,
                block_size=size,
                n_boot=n_boot,
                random_state=random_state,
                metrics=("auc",),
            )["auc"]
            deltas.append(result.point)
            if result.ci_95_lower * result.ci_95_upper > 0.0:
                fpr_ci += 1
            else:
                covered += 1
            if result.p_two_sided < 0.05:
                fpr_p += 1
        m = len(shifts)
        deltas_array = np.asarray(deltas, dtype=float)
        out[name] = {
            "block_size": size,
            "n_shift": m,
            "mean_delta_auc": float(deltas_array.mean()),
            "sd_delta_auc": float(deltas_array.std(ddof=1)) if m > 1 else None,
            "fpr_ci_excludes_zero": fpr_ci / m,
            "fpr_p_lt_0.05": fpr_p / m,
            "ci_coverage": covered / m,
        }
    return out


def placebo_from_predictions(
    run_dirs: list[Path],
    *,
    n_shift: int = 60,
    n_boot: int = 60,
    random_state: int = 42,
    blocks: tuple = PLACEBO_BLOCKS,
    large_n_threshold: int = 100_000,
    large_n_boot_divisor: int = 3,
) -> dict:
    """对每个 run 的已保存预测做标签循环平移安慰剂。

    成本与 `n × n_shift × n_boot` 成正比（bootstrap 内部是排序），
    因此对 5m 的大 n run 会自动降低 `n_boot`（见下面的
    `large_n_threshold`），以保证整轮审计的总耗时可控。降低 `n_boot`
    只让单次的区间更粗，不影响假阳性率的估计（后者由 `n_shift` 决定）。
    """
    per_run = []
    for run_dir in run_dirs:
        run_dir = Path(run_dir)
        try:
            summary = load_run_summary(run_dir)
        except FileNotFoundError:
            continue
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

        y = preds["target"].to_numpy(dtype=float)
        p_base = preds["p_base"].to_numpy(dtype=float)
        p_comb = preds["p_comb"].to_numpy(dtype=float)

        n = int(y.shape[0])
        boot_effective = (
            max(20, n_boot // large_n_boot_divisor)
            if n > large_n_threshold
            else n_boot
        )

        real = paired_block_bootstrap(
            y,
            p_base,
            p_comb,
            block_size=resolve_block_size(n, horizon),
            n_boot=boot_effective,
            random_state=random_state,
            metrics=("auc",),
        )["auc"]

        try:
            run_blocks = _placebo_blocks_for_run(
                y,
                p_base,
                p_comb,
                horizon=horizon,
                n_shift=n_shift,
                n_boot=boot_effective,
                random_state=random_state,
                blocks=blocks,
            )
        except ValueError as exc:
            run_blocks = {"error": str(exc)}

        per_run.append({
            "run_id": summary.get("run_id", run_dir.name),
            "timeframe": data_block.get("timeframe") or summary.get("timeframe"),
            "horizon": horizon,
            "n": n,
            "n_boot_effective": boot_effective,
            "real": real.as_dict(),
            "placebo": run_blocks,
        })

    # 汇总每个块长下的平均假阳性率
    summary_rows = {}
    for name, _ in blocks:
        fpr = [
            item["placebo"][name]["fpr_ci_excludes_zero"]
            for item in per_run
            if name in item.get("placebo", {})
        ]
        fpr_p = [
            item["placebo"][name]["fpr_p_lt_0.05"]
            for item in per_run
            if name in item.get("placebo", {})
        ]
        means = [
            item["placebo"][name]["mean_delta_auc"]
            for item in per_run
            if name in item.get("placebo", {})
        ]
        summary_rows[name] = {
            "n_runs": len(fpr),
            "mean_fpr_ci_excludes_zero": float(np.mean(fpr)) if fpr else None,
            "mean_fpr_p_lt_0.05": float(np.mean(fpr_p)) if fpr_p else None,
            "mean_null_delta_auc": float(np.mean(means)) if means else None,
        }

    return {
        "n_shift": n_shift,
        "n_boot": n_boot,
        "nominal_fpr": 0.05,
        "blocks": [name for name, _ in blocks],
        "per_run": per_run,
        "aggregate": summary_rows,
    }


# ---------------------------------------------------------------------------
# Q3b 安慰剂：1h 数据上重做 walk-forward
# ---------------------------------------------------------------------------
def _walk_forward_pooled(
    dataset: pd.DataFrame,
    feature_columns: list[str],
    *,
    test_start: str,
    test_end: str,
    train_months: int,
    retrain_months: int,
    min_train_samples: int,
    eligible: pd.Series,
) -> pd.DataFrame:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from alpha_lab.validation.walk_forward import make_walk_forward

    frames = []
    for fold in make_walk_forward(
        data=dataset,
        feature_columns=feature_columns,
        test_start=test_start,
        test_end=test_end,
        train_months=train_months,
        retrain_months=retrain_months,
        min_train_samples=min_train_samples,
        sample_eligible=eligible,
    ):
        train = dataset.loc[fold.train_idx]
        test = dataset.loc[fold.test_idx]
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, random_state=42),
        )
        model.fit(train[feature_columns], train["target"].astype(int))
        probability = model.predict_proba(test[feature_columns])[:, 1]
        frames.append(pd.DataFrame({
            "open_time": test["open_time"].to_numpy(),
            "target": test["target"].to_numpy(dtype=float),
            "probability": probability,
        }))
    if not frames:
        raise ValueError("no walk-forward folds")
    return pd.concat(frames, ignore_index=True)


def placebo_walk_forward(
    *,
    timeframe: str = "1h",
    horizon: int = 4,
    candidate_name: str = "vol24",
    n_shift: int = 30,
    n_boot: int = 250,
    random_state: int = 42,
    start: str = "2022-01-01",
    end: str = "2025-01-01",
    test_start: str = "2023-01-01",
    test_end: str = "2025-01-01",
    train_months: int = 12,
    retrain_months: int = 1,
    min_train_samples: int = 1000,
    data_dir: str | Path = "user_data/data/binance",
) -> dict:
    """端到端安慰剂：循环平移标签后重新训练 walk-forward 模型。

    这会把**模型拟合的过拟合偏差**也计入假阳性率，因此比
    `placebo_from_predictions` 更保守。
    """
    from alpha_lab.data.loader import load_ohlcv
    from alpha_lab.data.research_dataset import build_research_dataset
    from alpha_lab.factors.momentum import momentum
    from alpha_lab.baseline_cli import realized_volatility

    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        datadir=data_dir,
        start=start,
        end=end,
        data_format="feather",
    )
    features = pd.DataFrame({
        "mom4": momentum(market, periods=4),
        "mom24": momentum(market, periods=24),
        candidate_name: realized_volatility(
            market, 24, annualize=False
        ),
    }, index=market.frame.index)

    dataset = build_research_dataset(
        market=market, features=features, horizon=horizon
    ).reset_index(drop=True)

    columns = ["mom4", "mom24", candidate_name]
    eligible = pd.Series(
        np.isfinite(dataset[columns].to_numpy(dtype=float)).all(axis=1),
        index=dataset.index,
    )

    def run_once(target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        working = dataset.copy()
        working["target"] = target
        base = _walk_forward_pooled(
            working, ["mom4", "mom24"],
            test_start=test_start, test_end=test_end,
            train_months=train_months, retrain_months=retrain_months,
            min_train_samples=min_train_samples, eligible=eligible,
        )
        comb = _walk_forward_pooled(
            working, columns,
            test_start=test_start, test_end=test_end,
            train_months=train_months, retrain_months=retrain_months,
            min_train_samples=min_train_samples, eligible=eligible,
        )
        if not (base["open_time"].to_numpy() == comb["open_time"].to_numpy()).all():
            raise ValueError("baseline/combined timelines differ")
        return (
            base["target"].to_numpy(dtype=float),
            base["probability"].to_numpy(dtype=float),
            comb["probability"].to_numpy(dtype=float),
        )

    y0, pb0, pc0 = run_once(dataset["target"].to_numpy(dtype=float))
    real = paired_block_bootstrap(
        y0, pb0, pc0,
        block_size=resolve_block_size(y0.shape[0], horizon),
        n_boot=n_boot, random_state=random_state, metrics=("auc",),
    )["auc"]

    rng = np.random.default_rng(random_state)
    n = y0.shape[0]
    min_lag = max(horizon * 3, 24)
    count = min(n_shift, n - 2 * min_lag)
    shifts = rng.choice(np.arange(min_lag, n - min_lag), size=count, replace=False)

    per_block = {name: {"ci": 0, "p": 0, "deltas": []} for name, _ in PLACEBO_BLOCKS}
    for k in shifts:
        target = np.roll(y0, int(k))
        y, p_base, p_comb = run_once(target)
        for name, factor in PLACEBO_BLOCKS:
            size = 1 if factor is None else int(factor) * horizon
            result = paired_block_bootstrap(
                y, p_base, p_comb,
                block_size=int(min(max(size, 1), y.shape[0])),
                n_boot=n_boot, random_state=random_state, metrics=("auc",),
            )["auc"]
            if result.ci_95_lower * result.ci_95_upper > 0.0:
                per_block[name]["ci"] += 1
            if result.p_two_sided < 0.05:
                per_block[name]["p"] += 1
            per_block[name]["deltas"].append(result.point)

    out = {}
    for name, values in per_block.items():
        m = len(values["deltas"])
        deltas = np.asarray(values["deltas"], dtype=float)
        out[name] = {
            "block_size": 1 if name == "one" else (
                horizon if name == "horizon" else 3 * horizon
            ),
            "n_shift": m,
            "mean_delta_auc": float(deltas.mean()) if m else None,
            "fpr_ci_excludes_zero": values["ci"] / m if m else None,
            "fpr_p_lt_0.05": values["p"] / m if m else None,
        }

    return {
        "design": {
            "timeframe": timeframe,
            "horizon": horizon,
            "candidate": candidate_name,
            "n_shift": count,
            "n_boot": n_boot,
            "n_test_rows": int(n),
        },
        "real": real.as_dict(),
        "placebo": out,
    }


def render_selfcheck_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# Alpha Lab 统计方法自检（第四轮方法学审计，P0）")
    add("")
    add(f"- 方法版本：`{payload['method_version']}`")
    add("- 本报告只做**只读**计算；不修改任何 append-only 记录。")
    add("")

    add("## Q1 主指标是否存在事后选择")
    add("")
    q1 = payload["primary_metric_sensitivity"]
    add("把 11 个实验分别在三种主指标（+严格一致性模式）下重判：")
    add("")
    add("| 主指标变体 | 结论与已记录标签不同的实验数 |")
    add("|---|---|")
    for name, variant in q1["variants"].items():
        add(f"| `{name}` | {variant['n_flips_vs_recorded']} |")
    add("")
    add(
        f"是否存在「只有某个主指标才支持」的结论："
        f"**{q1['any_self_serving_flip']}**"
    )
    add("")
    add("结构原因（MDE / 实际意义阈值）：")
    add("")
    add("| run | ΔAUC | Δlogloss | ΔBrier |")
    add("|---|---|---|---|")
    for run_id, row in q1["structural_mde_over_threshold"].items():
        add(
            f"| `{run_id[:12]}` | "
            f"{row['auc_mde_over_threshold']:.2f} | "
            f"{row['logloss_mde_over_threshold']:.1f} | "
            f"{row['brier_mde_over_threshold']:.1f} |"
        )
    add("")
    add(
        "> Δlogloss/ΔBrier 的 MDE 是自身阈值的 30–200 倍；若把它们设为主指标，"
        "任何实验都不可能达到 SUPPORTED（这不是结果，而是**构造**）。"
    )
    add("")

    add("## Q2 BH 检验族定义")
    add("")
    q2 = payload["family_sensitivity"]
    add("| 族定义 | 族规模 | 结论（增量轴） |")
    add("|---|---|---|")
    for name, variant in q2.items():
        labels = ",".join(
            f"{run_id[:8]}:{label['incremental']}"
            for run_id, label in variant["labels"].items()
        )
        add(f"| `{name}` | {variant['family_size']} | {labels} |")
    add("")

    add("## Q3 block bootstrap 覆盖标签重叠依赖")
    add("")
    q3a = payload.get("placebo_predictions")
    if not q3a:
        add("（本次未运行 placebo，见 `--skip-placebo`）")
        add("")
    else:
        add(
            f"### Q3a 已保存预测的标签循环平移安慰剂"
            f"（n_shift={q3a['n_shift']}, n_boot={q3a['n_boot']}）"
        )
        add("")
        add("理想假阳性率 = 0.05。`block=1` 应显著高估显著性。")
        add("")
        add("| 块长 | run 数 | 平均假阳性率(CI 排除 0) | 平均假阳性率(p<0.05) | 零假设下平均 ΔAUC |")
        add("|---|---|---|---|---|")
        for name, row in q3a["aggregate"].items():
            add(
                f"| {name} | {row['n_runs']} | "
                f"{row['mean_fpr_ci_excludes_zero']} | "
                f"{row['mean_fpr_p_lt_0.05']} | "
                f"{row['mean_null_delta_auc']} |"
            )
        add("")
    if "placebo_walk_forward" in payload:
        q3b = payload["placebo_walk_forward"]
        add("### Q3b 端到端 walk-forward 安慰剂（含拟合偏差）")
        add("")
        add(
            f"设定：`{q3b['design']['timeframe']}` h={q3b['design']['horizon']}，"
            f"candidate=`{q3b['design']['candidate']}`，"
            f"n_shift={q3b['design']['n_shift']}，"
            f"测试段 n={q3b['design']['n_test_rows']}。"
        )
        add("")
        add("| 块长 | 平均 ΔAUC（零假设） | 假阳性率(CI 排除 0) |")
        add("|---|---|---|")
        for name, row in q3b["placebo"].items():
            add(
                f"| {name} | {row['mean_delta_auc']} | "
                f"{row['fpr_ci_excludes_zero']} |"
            )
        add("")
        add(
            f"（该设定下真实 ΔAUC = {q3b['real']['point']:.5f}，"
            f"95%CI [{q3b['real']['ci_95_lower']:.5f}, "
            f"{q3b['real']['ci_95_upper']:.5f}]）"
        )
        add("")

    add("## Q4 n_eff 与 MDE 口径一致性")
    add("")
    q4 = payload["neff_mde_consistency"]
    add("统一规则：")
    add("")
    for key, value in q4["canonical_rule"].items():
        add(f"- `{key}` = {value}")
    add("")
    add("| run | tf/h | n_eval | n/h | n/VIF | VIF | boot MDE | 全窗口规划放大 |")
    add("|---|---|---|---|---|---|---|---|")
    for row in q4["rows"]:
        scaling = row["mde_scaling_if_full_window_planning"]
        add(
            f"| `{row['run_id'][:12]}` | {row['timeframe']}/{row['horizon']} | "
            f"{row['n_eval']} | {row['n_eff_design_n_over_h']:.0f} | "
            f"{row['n_eff_autocorr_n_over_vif']:.0f} | "
            f"{row['variance_inflation']:.2f} | "
            f"{row['mde_auc_bootstrap']:.5f} | "
            f"{'—' if scaling is None else f'×{scaling:.2f}'} |"
        )
    add("")
    add(
        "> 不一致点：(a) 审计的 `n_eff` 表头是**设计口径** `n/horizon`，而"
        "预注册用的是 `n/max(h, VIF)`；逐根特征下两者相同，阶跃特征下不同。"
        "(b) 审计用测试段 n，预注册用全窗口 n，后者乐观约 1.2 倍。"
        "(c) 主 MDE 用 bootstrap 标准误（经验），规划 MDE 用解析 n_eff。"
    )
    add("")

    add("## 结论")
    add("")
    for bullet in payload.get("highlights", []):
        add(f"- {bullet}")
    add("")

    return "\n".join(lines)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    return value


def build_highlights(payload: dict) -> list[str]:
    bullets = []
    q1 = payload["primary_metric_sensitivity"]
    bullets.append(
        "Q1：主指标 ΔAUC 属于**事后选择**（先看到 Δlogloss/ΔBrier 噪声过大）。"
        f"在三种主指标下重判是否存在自利翻转：{q1['any_self_serving_flip']}。"
    )
    q3a = (payload.get("placebo_predictions") or {}).get("aggregate")
    if q3a and "horizon" in q3a and "one" in q3a:
        bullets.append(
            "Q3：block=horizon 的安慰剂假阳性率为 "
            f"{q3a['horizon']['mean_fpr_ci_excludes_zero']}（理想 0.05）；"
            f"block=1 为 {q3a['one']['mean_fpr_ci_excludes_zero']}。"
        )
    return bullets
