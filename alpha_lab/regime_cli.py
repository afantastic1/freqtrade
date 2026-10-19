"""Direction A：长周期制度条件化 alpha（预注册执行）。

对应 `user_data/research/alpha_lab/notes/prereg_direction_a_regime.md`。

本轮**只执行**具备足够功效的 5m / h=24 确认性配置：

- **A-1**：制度变量 `ER24`（效率比 = |Σ₂₄ r| / Σ₂₄|r|），预注册符号 `+1`
  （趋势强度高 → 反转更强，即 reversal × regime 交互 > 0）；
- **A-2**：制度变量 `log vol24`，预注册符号 `+1`。

A-3 / A-4（1h 的滚动方差比，MDE 0.17 / 0.28）按本轮范围**不运行**：
它们按预注册**不具备判定能力**，用户已要求"不运行功效不足的实验"。
本模块在报告里显式记录这一决定，避免被误读为静默丢弃。

判定沿用审计 policy（`auc = 0.005`, `α = 0.05`, `power = 0.80`）与
三标签 / 两轴合并规则，不做任何针对测试段的调参。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.baseline_cli import (
    _contiguity_mask,
    realized_volatility,
)
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset
from alpha_lab.factors.momentum import momentum
from alpha_lab.prereg_record import (
    append_decisions,
    ensure_analysis_run,
    record_reviews,
)
from alpha_lab.stats.audit import (
    _mechanism_verdict,
    monthly_gain_stability,
    rank_ic_sign_evidence,
)
from alpha_lab.stats.effective_samples import estimate_effective_samples
from alpha_lab.stats.metrics import (
    brier_loss_per_sample,
    logloss_per_sample,
)
from alpha_lab.stats.multiple_testing import adjust_pvalues
from alpha_lab.stats.paired_bootstrap import (
    paired_block_bootstrap,
    resolve_block_size,
)
from alpha_lab.stats.power import minimum_detectable_effect
from alpha_lab.stats.rank_ic import (
    bootstrap_rank_ic,
    bootstrap_rank_ic_difference,
    spearman_rank_ic,
)
from alpha_lab.stats.verdict import (
    MetricAssessment,
    VerdictPolicy,
    classify_result,
    combine_verdicts,
)

DATA_DIR = Path("user_data/data/binance")
RESEARCH_ROOT = Path("user_data/research/alpha_lab")
NOTES = RESEARCH_ROOT / "notes"

START = "2022-01-01"
END = "2025-01-01"
TRAIN = ("2022-01-01", "2022-10-01")
TEST = ("2023-01-01", "2025-01-01")

METHOD_VERSION = "prereg-a-v3"
DECISION_LOG = "prereg_decisions.jsonl"

METRICS = ("auc", "logloss", "brier")
THRESHOLD_SCALES = (0.5, 0.8, 1.0, 1.5, 2.0)
BLOCK_MULTIPLIERS = (1, 3, 8)

CONFIGS = (
    {
        "id": "A-1",
        "timeframe": "5m",
        "horizon": 24,
        "regime_kind": "efficiency_ratio",
        "regime_window": 24,
        "regime_label": "ER24",
        "role": "confirmatory",
        "preregistered_sign": +1,
    },
    {
        "id": "A-2",
        "timeframe": "5m",
        "horizon": 24,
        "regime_kind": "log_volatility",
        "regime_window": 24,
        "regime_label": "log_vol24",
        "role": "confirmatory",
        "preregistered_sign": +1,
    },
)

NOT_RUN = (
    {
        "id": "A-3",
        "setting": "1h, h=4, regime=VR(24) rolling 96 bars",
        "reason": "预注册 MDE(ΔRankIC)=0.170；本轮范围只运行具备足够功效的配置",
    },
    {
        "id": "A-4",
        "setting": "1h, h=4, regime=VR(64) rolling 256 bars",
        "reason": "预注册 MDE(ΔRankIC)=0.281；本轮范围只运行具备足够功效的配置",
    },
)


# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------
def efficiency_ratio(market, periods: int = 24) -> pd.Series:
    """效率比：|Σ r| / Σ|r|，严格因果（只用截至 t 的收盘价）。

    ER 高 = 路径接近单边趋势；ER 低 = 来回震荡。取值在 [0, 1]。
    """
    from freqtrade.exchange import timeframe_to_seconds

    if periods <= 1:
        raise ValueError("periods must be >= 2")

    frame = market.frame
    log_return = np.log(frame["close"]).diff()

    net = log_return.rolling(periods, min_periods=periods).sum().abs()
    gross = log_return.abs().rolling(periods, min_periods=periods).sum()

    ratio = net / gross.replace(0.0, np.nan)
    ratio = ratio.mask(
        ~_contiguity_mask(
            frame, periods, timeframe_to_seconds(market.timeframe)
        )
    )
    return ratio.rename(f"er_{periods}")


def log_realized_volatility(market, periods: int = 24) -> pd.Series:
    vol = realized_volatility(market, periods=periods, annualize=False)
    return np.log(vol.replace(0.0, np.nan)).rename(f"log_vol_{periods}")


def build_regime(market, kind: str, window: int) -> pd.Series:
    if kind == "efficiency_ratio":
        return efficiency_ratio(market, window)
    if kind == "log_volatility":
        return log_realized_volatility(market, window)
    raise ValueError(f"unknown regime kind: {kind}")


# ---------------------------------------------------------------------------
# 数据集
# ---------------------------------------------------------------------------
def build_dataset(market, config: dict) -> pd.DataFrame:
    features = pd.DataFrame({
        # 用 rev4 = -mom4。logistic 的 ΔAUC 对因子整体取反不变，
        # 因此使用 rev4 不会改变增量端点，只让交互项系数可直接对应
        # 预注册方向（反转 × regime > 0）。
        "rev4": -momentum(market, periods=4),
        "mom24": momentum(market, periods=24),
        "regime": build_regime(
            market, config["regime_kind"], config["regime_window"]
        ),
    }, index=market.frame.index)

    dataset = build_research_dataset(
        market=market, features=features, horizon=config["horizon"]
    )
    return dataset.reset_index(drop=True)


def _segment_mask(dataset, window) -> pd.Series:
    start = pd.Timestamp(window[0], tz="UTC")
    end = pd.Timestamp(window[1], tz="UTC")
    return (
        (dataset["open_time"] >= start) & (dataset["open_time"] < end)
    ).fillna(False)


def _zparams(train: pd.DataFrame, columns: list[str]) -> dict:
    return {
        column: (
            float(train[column].mean()),
            float(train[column].std(ddof=0)),
        )
        for column in columns
    }


def _zscore(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    out = {}
    for column, (mean, std) in params.items():
        if not np.isfinite(std) or std <= 0:
            raise ValueError(f"degenerate train std for {column}")
        out[f"z_{column}"] = (frame[column] - mean) / std
    return pd.DataFrame(out, index=frame.index)


def _fit_predict(
    train: pd.DataFrame, test: pd.DataFrame, columns: list[str]
) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, random_state=42),
    )
    model.fit(train[columns], train["target"].astype(int))
    return model.predict_proba(test[columns])[:, 1]


# ---------------------------------------------------------------------------
# 分层 RankIC（机制方向）
# ---------------------------------------------------------------------------
def stratified_rank_ic(
    test: pd.DataFrame,
    *,
    signal: str = "rev4",
    label: str = "reference_return",
    regime: str = "regime",
    threshold: float,
    horizon: int,
    n_boot: int,
    random_state: int,
) -> dict:
    """按 regime 分层的 RankIC 差异（高 regime − 低 regime）。

    预注册符号 +1 表示"高 regime 下反转更强"。
    """
    high = (test[regime] > threshold)
    difference = bootstrap_rank_ic_difference(
        test[signal],
        test[label],
        high,
        horizon=horizon,
        n_boot=n_boot,
        random_state=random_state,
    )

    monthly = []
    for month, group in test.groupby(test["open_time"].dt.strftime("%Y-%m")):
        hi = group[group[regime] > threshold]
        lo = group[group[regime] <= threshold]
        if len(hi) < 10 or len(lo) < 10:
            continue
        value = spearman_rank_ic(hi[signal], hi[label]) - spearman_rank_ic(
            lo[signal], lo[label]
        )
        if np.isfinite(value):
            monthly.append({"month": str(month), "difference": float(value)})

    if monthly:
        values = np.asarray([row["difference"] for row in monthly], dtype=float)
        mean_difference = float(values.mean())
        negative_ratio = float((values < 0).mean())
    else:
        mean_difference = float("nan")
        negative_ratio = None

    return {
        "threshold_train_median": float(threshold),
        "point": difference["point"],
        "ci_95_lower": difference["ci_95_lower"],
        "ci_95_upper": difference["ci_95_upper"],
        "p_two_sided": difference["p_two_sided"],
        "n_group_high": difference["n_group_true"],
        "n_group_low": difference["n_group_false"],
        "monthly_mean_difference": mean_difference,
        "monthly_negative_ratio": negative_ratio,
        "monthly_months": len(monthly),
        "monthly": monthly,
    }


# ---------------------------------------------------------------------------
# 单个配置
# ---------------------------------------------------------------------------
def run_config(
    market,
    config: dict,
    *,
    n_boot: int = 1000,
    rank_ic_boot: int = 500,
    random_state: int = 42,
) -> dict:
    dataset = build_dataset(market, config)
    train_mask = _segment_mask(dataset, TRAIN)
    test_mask = _segment_mask(dataset, TEST)

    columns = ["rev4", "mom24", "regime"]
    train_valid = (
        train_mask
        & dataset[columns].notna().all(axis=1)
        & dataset["target"].notna()
    )
    test_valid = (
        test_mask
        & dataset[columns].notna().all(axis=1)
        & dataset["target"].notna()
    )

    params = _zparams(dataset.loc[train_valid], columns)
    z = _zscore(dataset, params)

    train = dataset.loc[train_valid].copy()
    for column in columns:
        train[f"z_{column}"] = z.loc[train_valid, f"z_{column}"]
    train["z_interaction"] = train["z_rev4"] * train["z_regime"]

    test = dataset.loc[test_valid].copy()
    for column in columns:
        test[f"z_{column}"] = z.loc[test_valid, f"z_{column}"]
    test["z_interaction"] = test["z_rev4"] * test["z_regime"]

    base_cols = ["z_rev4", "z_mom24"]
    interaction_cols = base_cols + ["z_regime", "z_interaction"]

    p_base = _fit_predict(train, test, base_cols)
    p_interaction = _fit_predict(train, test, interaction_cols)

    y = test["target"].to_numpy(dtype=float)
    horizon = config["horizon"]
    regime_window = config["regime_window"]
    block = max(horizon, regime_window)

    bootstrap = paired_block_bootstrap(
        y,
        p_base,
        p_interaction,
        block_size=resolve_block_size(y.shape[0], block),
        n_boot=n_boot,
        random_state=random_state,
    )

    gains_logloss = logloss_per_sample(y, p_base) - logloss_per_sample(
        y, p_interaction
    )
    effective = estimate_effective_samples(gains_logloss, horizon=horizon)
    stability = monthly_gain_stability(test["open_time"], gains_logloss)

    # 冗余度：交互特征与三个基线因子的最大 |Spearman|
    similarities = {}
    for name in ("rev4", "mom24", "regime"):
        similarities[name] = float(
            abs(spearman_rank_ic(test["z_interaction"], test[name]))
        )
    max_abs_spearman = max(similarities.values())

    # 制度状态转移与驻留（诊断 n_eff 假设）
    high = (test["regime"] > float(dataset.loc[train_valid, "regime"].median()))
    switches = int((high.astype(int).diff().abs() > 0).sum())
    mean_dwell = float(len(high) / switches) if switches else None

    threshold = float(dataset.loc[train_valid, "regime"].median())
    mechanism = stratified_rank_ic(
        test,
        signal="rev4",
        label="reference_return",
        regime="regime",
        threshold=threshold,
        horizon=horizon,
        n_boot=rank_ic_boot,
        random_state=random_state,
    )

    # 无条件反转参照（同一设定）
    unconditional = bootstrap_rank_ic(
        test["rev4"],
        test["reference_return"],
        horizon=horizon,
        n_boot=rank_ic_boot,
        random_state=random_state,
    )

    # 块长敏感性：max(h, L) × {1, 3, 8}
    robustness = {}
    for multiplier in BLOCK_MULTIPLIERS:
        requested = block * multiplier
        if requested > y.shape[0]:
            continue
        size = resolve_block_size(y.shape[0], block, requested=requested)
        robustness[str(multiplier)] = {
            "block_size": size,
            "auc": paired_block_bootstrap(
                y, p_base, p_interaction,
                block_size=size, n_boot=max(200, n_boot // 4),
                random_state=random_state, metrics=("auc",),
            )["auc"].as_dict(),
        }

    mechanisms_evidence = rank_ic_sign_evidence(
        mechanism["point"],
        mechanism["monthly_negative_ratio"],
        mechanism["monthly_months"],
        n_eff=float(mechanism["monthly_months"]),
    )

    return {
        "config": config,
        "n_train": int(train_valid.sum()),
        "n_test": int(y.shape[0]),
        "block_size": int(resolve_block_size(y.shape[0], block)),
        "block_rule": f"max(horizon={horizon}, regime_window={regime_window})",
        "n_eff": {
            "design": effective.n_eff_design,
            "autocorr": effective.n_eff_autocorr,
            "conservative": effective.conservative_n_eff,
            "variance_inflation": effective.variance_inflation,
            "max_lag": effective.max_lag,
        },
        "metrics": {
            name: bootstrap[name].as_dict() for name in METRICS
        },
        "mde_auc_bootstrap": float(
            minimum_detectable_effect(bootstrap["auc"].boot_sd)
        ),
        "stability": stability,
        "redundancy": {
            "max_abs_spearman": max_abs_spearman,
            "per_factor": similarities,
        },
        "regime_switches": switches,
        "regime_mean_dwell_rows": mean_dwell,
        "mechanism": mechanism,
        "mechanism_evidence": mechanisms_evidence,
        "unconditional_reversal_rank_ic": unconditional,
        "block_sensitivity": robustness,
        "train_parameter_source": "train segment only",
    }


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def _assessments(result: dict, policy: VerdictPolicy) -> dict:
    return {
        name: MetricAssessment(
            metric=name,
            point=result["metrics"][name]["point"],
            ci_lower=result["metrics"][name]["ci_95_lower"],
            ci_upper=result["metrics"][name]["ci_95_upper"],
            mde=minimum_detectable_effect(result["metrics"][name]["boot_sd"]),
            practical_threshold=policy.threshold_for(name),
            p_two_sided=result["metrics"][name]["p_two_sided"],
        )
        for name in METRICS
    }


def finalize(results: list[dict], policy: VerdictPolicy | None = None) -> dict:
    """家族内 BH 校正 + 两轴判定。"""
    policy = policy or VerdictPolicy()
    raw_p = np.asarray(
        [result["metrics"]["auc"]["p_two_sided"] for result in results],
        dtype=float,
    )
    adjusted = adjust_pvalues(raw_p)["benjamini_hochberg"]

    # 机制轴同样做 BH 校正（族 = 本方向的确认性配置）
    mechanism_p = np.asarray(
        [
            result["mechanism_evidence"]["sign_test_p"]
            if result["mechanism_evidence"]["sign_test_p"] is not None
            else 1.0
            for result in results
        ],
        dtype=float,
    )
    mechanism_adjusted = adjust_pvalues(mechanism_p)["benjamini_hochberg"]
    for position, result in enumerate(results):
        result["mechanism_evidence"]["sign_test_p_adjusted"] = float(
            mechanism_adjusted[position]
        )

    for position, result in enumerate(results):
        assessments = _assessments(result, policy)
        assessments["auc"] = MetricAssessment(
            metric="auc",
            point=assessments["auc"].point,
            ci_lower=assessments["auc"].ci_lower,
            ci_upper=assessments["auc"].ci_upper,
            mde=assessments["auc"].mde,
            practical_threshold=assessments["auc"].practical_threshold,
            adjusted_p=float(adjusted[position]),
            p_two_sided=assessments["auc"].p_two_sided,
        )
        result["adjusted_p_within_direction"] = float(adjusted[position])
        result["p_auc_raw"] = float(raw_p[position])

        incremental = classify_result(
            assessments,
            primary_metric="auc",
            stability_ratio=result["stability"].get("positive_ratio"),
            max_abs_spearman=result["redundancy"]["max_abs_spearman"],
            policy=policy,
        )
        direction_consistent = (
            int(np.sign(result["mechanism"]["point"]))
            == int(result["config"]["preregistered_sign"])
            if np.isfinite(result["mechanism"]["point"])
            else None
        )
        observed = int(np.sign(result["mechanism"]["point"])) if direction_consistent is not None else None
        result["mechanism_evidence"]["observed_sign"] = observed
        ratio = result["mechanism"].get("monthly_negative_ratio")
        result["mechanism_evidence"]["monthly_observed_sign"] = (
            -1 if ratio is not None and ratio > 0.5 else
            1 if ratio is not None and ratio < 0.5 else None
        )
        mechanism = _mechanism_verdict(
            result["mechanism_evidence"], direction_consistent, policy
        )
        result["direction_consistent"] = direction_consistent
        result["verdict"] = incremental.as_dict()
        result["mechanism_verdict"] = mechanism
        result["overall_verdict"] = combine_verdicts(incremental, mechanism)

        # 阈值敏感性
        sensitivity = {}
        for scale in THRESHOLD_SCALES:
            alt = VerdictPolicy(
                alpha=policy.alpha,
                power=policy.power,
                redundancy_threshold=policy.redundancy_threshold,
                min_stability_ratio=policy.min_stability_ratio,
                require_p_adjusted=policy.require_p_adjusted,
                practical_thresholds={
                    name: value * scale
                    for name, value in policy.practical_thresholds.items()
                },
            )
            alt_assessments = _assessments(result, alt)
            alt_assessments["auc"] = MetricAssessment(
                metric="auc",
                point=alt_assessments["auc"].point,
                ci_lower=alt_assessments["auc"].ci_lower,
                ci_upper=alt_assessments["auc"].ci_upper,
                mde=alt_assessments["auc"].mde,
                practical_threshold=alt_assessments["auc"].practical_threshold,
                adjusted_p=float(adjusted[position]),
                p_two_sided=alt_assessments["auc"].p_two_sided,
            )
            alt_incremental = classify_result(
                alt_assessments,
                primary_metric="auc",
                stability_ratio=result["stability"].get("positive_ratio"),
                max_abs_spearman=result["redundancy"]["max_abs_spearman"],
                policy=alt,
            )
            sensitivity[f"x{scale}"] = alt_incremental.label
        result["threshold_sensitivity"] = sensitivity

    return {
        "family_size": len(results),
        "policy": {
            "alpha": policy.alpha,
            "power": policy.power,
            "redundancy_threshold": policy.redundancy_threshold,
            "min_stability_ratio": policy.min_stability_ratio,
            "practical_thresholds": dict(policy.practical_thresholds),
        },
        "results": results,
    }


def run_direction_a(
    *,
    n_boot: int = 1000,
    rank_ic_boot: int = 500,
    random_state: int = 42,
) -> dict:
    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe="5m",
        datadir=DATA_DIR,
        start=START,
        end=END,
        data_format="feather",
    )

    results = [
        run_config(
            market, config,
            n_boot=n_boot, rank_ic_boot=rank_ic_boot,
            random_state=random_state,
        )
        for config in CONFIGS
    ]
    payload = finalize(results)
    payload["method_version"] = METHOD_VERSION
    payload["not_run"] = list(NOT_RUN)
    payload["splits"] = {"train": list(TRAIN), "test": list(TEST)}
    payload["data"] = {"symbol": "BTC/USDT:USDT", "timeframe": "5m",
                       "range": [START, END], "sealed": "2025-01-01+"}
    payload["n_boot"] = n_boot
    payload["rank_ic_boot"] = rank_ic_boot
    payload["random_state"] = random_state
    return payload


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _fmt(value, digits=4):
    if value is None:
        return "—"
    if isinstance(value, float):
        if not np.isfinite(value):
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# 预注册实验执行报告 A：长周期制度条件化 alpha")
    add("")
    add(f"- 方法版本：`{payload['method_version']}`")
    add(
        f"- 数据：{payload['data']['symbol']} {payload['data']['timeframe']}，"
        f"区间 `{payload['data']['range'][0]}` ~ `{payload['data']['range'][1]}`"
        f"（{payload['data']['sealed']} 不读取）"
    )
    add(
        f"- 切分：训练 `{payload['splits']['train']}`；"
        f"测试 `{payload['splits']['test']}`"
    )
    add(
        f"- bootstrap：主 n_boot={payload['n_boot']}，"
        f"RankIC n_boot={payload['rank_ic_boot']}，"
        f"random_state={payload['random_state']}"
    )
    add(
        f"- 判定 policy：{payload['policy']}（族 = 本方向 {payload['family_size']} 个确认性配置）"
    )
    add("")
    add("## 未运行的预注册配置")
    add("")
    add("| ID | 设定 | 原因 |")
    add("|---|---|---|")
    for item in payload["not_run"]:
        add(f"| {item['id']} | {item['setting']} | {item['reason']} |")
    add("")

    for result in payload["results"]:
        config = result["config"]
        add(f"## {config['id']}：{config['regime_label']}（{config['role']}）")
        add("")
        add(
            f"- 设定：{config['timeframe']} / h={config['horizon']} / "
            f"regime={config['regime_label']}；预注册符号 "
            f"{config['preregistered_sign']:+d}"
        )
        add(
            f"- n_train={result['n_train']}，n_test={result['n_test']}，"
            f"块长={result['block_size']}（{result['block_rule']}）"
        )
        add(
            f"- n_eff(design)={_fmt(result['n_eff']['design'], 0)}，"
            f"VIF={_fmt(result['n_eff']['variance_inflation'], 2)}，"
            f"MDE(ΔAUC, bootstrap)={_fmt(result['mde_auc_bootstrap'], 5)}"
        )
        add("")
        add("| 指标 | 点估计 | 95%CI | p | BH 调整 p | 阈值 | MDE ≤ 阈值 |")
        add("|---|---|---|---|---|---|---|")
        for name in METRICS:
            item = result["metrics"][name]
            mde = minimum_detectable_effect(item["boot_sd"])
            add(
                f"| Δ{name} | {_fmt(item['point'], 5)} | "
                f"[{_fmt(item['ci_95_lower'], 5)}, {_fmt(item['ci_95_upper'], 5)}] | "
                f"{_fmt(item['p_two_sided'], 4)} | "
                f"{_fmt(result['adjusted_p_within_direction'], 4) if name == 'auc' else '—'} | "
                f"{_fmt(payload['policy']['practical_thresholds'][name], 6)} | "
                f"{'是' if mde <= payload['policy']['practical_thresholds'][name] else '否'} |"
            )
        add("")
        add(
            f"- 月度增益正比：{_fmt(result['stability'].get('positive_ratio'), 2)}"
            f"（{result['stability'].get('positive_months')}/"
            f"{result['stability'].get('months')}）"
        )
        add(
            f"- 冗余度 max|ρ|（交互特征 vs rev4/mom24/regime）："
            f"{_fmt(result['redundancy']['max_abs_spearman'], 3)}"
            f"（{ {k: round(v,3) for k, v in result['redundancy']['per_factor'].items()} }）"
        )
        add(
            f"- 制度状态：切换 {result['regime_switches']} 次，"
            f"平均驻留 {_fmt(result['regime_mean_dwell_rows'], 1)} 根"
        )
        mech = result["mechanism"]
        add(
            f"- 分层 RankIC 差异（高−低，rev4）：{_fmt(mech['point'], 4)} "
            f"95%CI [{_fmt(mech['ci_95_lower'], 4)}, {_fmt(mech['ci_95_upper'], 4)}]；"
            f"逐月均值 {_fmt(mech['monthly_mean_difference'], 4)}，"
            f"逐月负比 {_fmt(mech['monthly_negative_ratio'], 2)}"
            f"（{mech['monthly_months']} 个月）"
        )
        uncond = result["unconditional_reversal_rank_ic"]
        add(
            f"- 无条件反转参照（同设定 rev4）：RankIC "
            f"{_fmt(uncond['point'], 4)} 95%CI "
            f"[{_fmt(uncond['ci_95_lower'], 4)}, {_fmt(uncond['ci_95_upper'], 4)}]"
        )
        add("")
        add("块长敏感性（ΔAUC）：")
        add("")
        add("| 倍数 | 块长 | ΔAUC | 95%CI |")
        add("|---|---|---|---|")
        for key, item in sorted(result["block_sensitivity"].items(), key=lambda kv: int(kv[0])):
            add(
                f"| ×{key} | {item['block_size']} | "
                f"{_fmt(item['auc']['point'], 5)} | "
                f"[{_fmt(item['auc']['ci_95_lower'], 5)}, "
                f"{_fmt(item['auc']['ci_95_upper'], 5)}] |"
            )
        add("")
        add("阈值敏感性（增量轴标签）：")
        add("")
        add("| " + " | ".join(f"×{s}" for s in THRESHOLD_SCALES) + " |")
        add("|---" * len(THRESHOLD_SCALES) + "|")
        add(
            "| "
            + " | ".join(
                str(result["threshold_sensitivity"][f"x{s}"])
                for s in THRESHOLD_SCALES
            )
            + " |"
        )
        add("")
        add(
            f"**增量判定：{result['verdict']['label']}** —— "
            f"{', '.join(result['verdict']['reasons'])}"
        )
        add("")
        add(
            f"**机制判定：{result['mechanism_verdict']['label']}** —— "
            f"{', '.join(result['mechanism_verdict']['reasons'])}"
        )
        add("")
        add(f"**最终：{result['overall_verdict']['label']}**")
        add("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------
def _review_row(result: dict, run_id: str, random_state: int, n_boot: int) -> dict:
    policy = VerdictPolicy()
    return {
        "run_id": run_id,
        "analysis_id": f"prereg-a-{result['config']['id']}",
        "config_id": result["config"]["id"],
        "timeframe": result["config"]["timeframe"],
        "horizon": result["config"]["horizon"],
        "n": result["n_test"],
        "n_eff": {"design": result["n_eff"]["design"]},
        "block_size": result["block_size"],
        "bootstrap_samples": n_boot,
        "random_state": random_state,
        "primary_metric": "auc",
        "metrics": {
            name: {
                **result["metrics"][name],
                "mde": minimum_detectable_effect(
                    result["metrics"][name]["boot_sd"]
                ),
                "practical_threshold": policy.threshold_for(name),
                "adjusted_p": (
                    result["adjusted_p_within_direction"] if name == "auc" else None
                ),
            }
            for name in METRICS
        },
        "verdict": result["verdict"],
        "mechanism_verdict": result["mechanism_verdict"],
        "overall_verdict": result["overall_verdict"],
    }


def record(payload: dict, *, random_state: int, n_boot: int) -> dict:
    """每个配置一条独立的 experiments 行 + 一条 statistical_reviews 行。

    为什么要**逐配置**建 run：`statistical_reviews` 的幂等键是
    `(run_id, method_version, n_boot, random_state)`。若两个方向共用一个
    run_id，多条评议会互相覆盖（INSERT OR IGNORE）。因此每个配置必须是
    自己的 run。
    """
    run_ids = []
    review_ids = []
    decisions = []

    for result in payload["results"]:
        config = result["config"]
        run_id = ensure_analysis_run(
            RESEARCH_ROOT,
            analysis_id=f"prereg-a-{config['id']}",
            config={
                "analysis": "prereg_direction_a_regime",
                "method_version": METHOD_VERSION,
                "config_id": config["id"],
                "setting": config,
                "splits": payload["splits"],
                "n_boot": n_boot,
                "random_state": random_state,
            },
            result_dir=NOTES,
            code_version=METHOD_VERSION,
        )
        run_ids.append(run_id)
        review_ids.extend(
            record_reviews(
                RESEARCH_ROOT,
                [_review_row(result, run_id, random_state, n_boot)],
                method_version=METHOD_VERSION,
            )
        )
        decisions.append(
            {
                "analysis_id": f"prereg-a-{config['id']}",
                "run_id": run_id,
                "hypothesis": (
                    f"{config['id']}: 反转强度随 {config['regime_label']} "
                    f"上升（预注册符号 {config['preregistered_sign']:+d}）"
                ),
                "conclusion": {
                    "overall_verdict": result["overall_verdict"]["label"],
                    "incremental_verdict": result["verdict"]["label"],
                    "incremental_reasons": list(result["verdict"]["reasons"]),
                    "mechanism_verdict": result["mechanism_verdict"]["label"],
                    "mechanism_reasons": list(
                        result["mechanism_verdict"]["reasons"]
                    ),
                    "delta_auc": result["metrics"]["auc"]["point"],
                    "delta_auc_ci": [
                        result["metrics"]["auc"]["ci_95_lower"],
                        result["metrics"]["auc"]["ci_95_upper"],
                    ],
                    "mde_auc": result["mde_auc_bootstrap"],
                    "p_adjusted_within_direction": result[
                        "adjusted_p_within_direction"
                    ],
                },
                "next_hypothesis": None,
                "next_actions": (
                    ["stop"]
                    if result["overall_verdict"]["label"] != "SUPPORTED"
                    else ["consider independent replication"]
                ),
            }
        )

    path = append_decisions(RESEARCH_ROOT, decisions, log_name=DECISION_LOG)

    return {
        "run_ids": run_ids,
        "review_ids": review_ids,
        "decision_log": str(path),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="alpha_lab.regime_cli")
    parser.add_argument("--n-boot", type=int, default=1000)
    parser.add_argument("--rank-ic-boot", type=int, default=500)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--out-dir", default=str(NOTES))
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--record-only",
        action="store_true",
        help="只读取已生成的 JSON 并追加记录（不重算）",
    )
    args = parser.parse_args(argv)

    if args.record_only:
        path = Path(args.out_dir) / "prereg_a_results.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        info = record(
            payload,
            random_state=payload["random_state"],
            n_boot=payload["n_boot"],
        )
        print(f"recorded: {info}")
        return 0

    payload = run_direction_a(
        n_boot=args.n_boot,
        rank_ic_boot=args.rank_ic_boot,
        random_state=args.random_state,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "prereg_a_results.json"
    md_path = out_dir / "prereg_a_results.md"

    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False,
                   ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    for result in payload["results"]:
        print(
            f"{result['config']['id']} {result['config']['regime_label']}: "
            f"ΔAUC={result['metrics']['auc']['point']:.5f} "
            f"CI=[{result['metrics']['auc']['ci_95_lower']:.5f},"
            f"{result['metrics']['auc']['ci_95_upper']:.5f}] "
            f"p_adj={result['adjusted_p_within_direction']:.4f} "
            f"incremental={result['verdict']['label']} "
            f"mechanism={result['mechanism_verdict']['label']} "
            f"overall={result['overall_verdict']['label']}"
        )

    if args.record:
        info = record(payload, random_state=args.random_state, n_boot=args.n_boot)
        print(f"recorded: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
