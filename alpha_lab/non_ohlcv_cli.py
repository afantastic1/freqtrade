"""Direction B：非 OHLCV 因子（预注册执行）。

对应 `user_data/research/alpha_lab/notes/prereg_direction_b_non_ohlcv.md`。

本轮**只执行**满足预注册条件（非 OHLCV、可解释、且 >=1 个设定达到
MDE(RankIC) <= 0.05）的检验：

- **H-B1**：`basis_dev`（永续/现货基差相对其 30 天因果滚动均值的偏离）
  - 主设定 5m / h=12（预注册 MDE ≈ 0.0173，全仓库最优）；
  - 次设定 1h / h=4（预注册 MDE ≈ 0.0423）。
- **H-B4**：把 `basis_dev` 相对 OHLCV 基线（`mom4 + mom12 + vol24`）
  残差化，再看残差 RankIC 的振幅是否仍 >= 原始的 50%。

**不运行** H-B2 / H-B3（资金费率事件检验）：预注册自身给出的
MDE 上限 ≈ 0.047–0.050（8h 阶跃导致的有效样本瓶颈），属于
"功效不足"，用户已明确要求不运行。本模块只**报告**其功效上限。

**不运行** open_interest：数据审计显示研究区间内 0 行，不可检验。

主端点 = 单因子 RankIC（逐月 + 全段，moving-block bootstrap，
块长 = max(h, 特征周期)）；次端点 = 残差化 RankIC。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.baseline_cli import realized_volatility
from alpha_lab.data.loader import from_freqtrade_dataframe, load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset
from alpha_lab.factors.momentum import momentum
from alpha_lab.prereg_record import (
    append_decisions,
    ensure_analysis_run,
    record_reviews,
)
from alpha_lab.stats.audit import _mechanism_verdict, rank_ic_sign_evidence
from alpha_lab.stats.power import minimum_detectable_effect
from alpha_lab.stats.multiple_testing import adjust_pvalues
from alpha_lab.stats.rank_ic import bootstrap_rank_ic, spearman_rank_ic
from alpha_lab.stats.verdict import (
    DEFAULT_PRACTICAL_THRESHOLDS,
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

METHOD_VERSION = "prereg-b-v2-centered-null"
DECISION_LOG = "prereg_decisions.jsonl"

# RankIC 没有冻结阈值（审计的冻结阈值是 ΔAUC=0.005）。本轮在**运行前**
# 声明一个 RankIC 的"实际意义"下限，并显式标注这是本轮新增口径，
# 以便用与审计**完全相同**的三分类规则（design_adequate / resolvable /
# rules_out_practical）来判定单因子端点。
RANK_IC_PRACTICAL_THRESHOLD = 0.01
BLOCK_MULTIPLIERS = (1, 3, 8)

SETTINGS = (
    {
        "id": "H-B1a",
        "timeframe": "5m",
        "horizon": 12,
        "window_days": 30,
        "role": "primary",
        "preregistered_sign": -1,
    },
    {
        "id": "H-B1b",
        "timeframe": "1h",
        "horizon": 4,
        "window_days": 30,
        "role": "secondary",
        "preregistered_sign": -1,
    },
)

H_B4_BASE = {
    "id": "H-B4",
    "timeframe": "5m",
    "horizon": 12,
    "window_days": 30,
    "role": "residualized",
    "preregistered_sign": -1,
}

NOT_RUN = (
    {
        "id": "H-B2",
        "setting": "funding_rate 事件检验（固定绝对阈值 > +4bps / < -2bps），1h/h4 与 5m/h12",
        "reason": "预注册 MDE(RankIC) 上限 ≈ 0.047–0.050（8h 阶跃，n_eff≈3183）；功效不足，本轮不运行",
    },
    {
        "id": "H-B3",
        "setting": "资金费率惯性 / 累积资金费率",
        "reason": "预注册列为探索性、不入显著性族；本轮范围不运行",
    },
    {
        "id": "OI",
        "setting": "open_interest 相关检验",
        "reason": "数据审计：研究区间 [2022-01-01, 2025-01-01) 内 0 行，不可检验",
    },
)


def _policy() -> VerdictPolicy:
    return VerdictPolicy(
        practical_thresholds={
            **DEFAULT_PRACTICAL_THRESHOLDS,
            "rank_ic": RANK_IC_PRACTICAL_THRESHOLD,
        }
    )


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
def load_spot(timeframe: str, start: str, end: str):
    """读取现货 OHLCV（CandleType.SPOT，pair 无结算后缀）。"""
    from freqtrade.configuration.timerange import TimeRange
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    timerange = TimeRange(
        starttype="date",
        stoptype="date",
        startts=int(start_ts.timestamp()),
        stopts=int(end_ts.timestamp()),
    )
    frame = load_pair_history(
        pair="BTC/USDT",
        timeframe=timeframe,
        datadir=DATA_DIR,
        timerange=timerange,
        data_format="feather",
        candle_type=CandleType.SPOT,
        fill_up_missing=False,
        drop_incomplete=False,
    )
    if frame is None or frame.empty:
        raise FileNotFoundError(f"missing spot history for {timeframe}")
    return from_freqtrade_dataframe(
        frame, symbol="BTC/USDT", timeframe=timeframe
    )


def bars_per_day(timeframe: str) -> int:
    from freqtrade.exchange import timeframe_to_seconds

    return int(86400 / timeframe_to_seconds(timeframe))


def basis_frame(timeframe: str, window_days: int, start: str, end: str) -> pd.DataFrame:
    """基差及其相对自身 30 天因果滚动均值的偏离。

    基差水平项存在无法解释的稳定负偏移（永续长期低于现货），
    因此预注册规则是**只使用偏离**；水平项一并报告以便读者看到。
    """
    perp = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        datadir=DATA_DIR,
        start=start,
        end=end,
        data_format="feather",
    )
    spot = load_spot(timeframe, start, end)

    merged = perp.frame[["open_time", "close"]].merge(
        spot.frame[["open_time", "close"]],
        on="open_time",
        how="inner",
        suffixes=("_perp", "_spot"),
    )
    merged = merged.sort_values("open_time").reset_index(drop=True)

    basis = merged["close_perp"] / merged["close_spot"] - 1.0
    window = int(window_days * bars_per_day(timeframe))
    trailing_mean = basis.shift(1).rolling(window, min_periods=window).mean()
    basis_dev = basis - trailing_mean
    basis_change = basis - basis.shift(1)

    out = pd.DataFrame(
        {
            "open_time": merged["open_time"],
            "basis": basis,
            "basis_dev": basis_dev,
            "basis_change": basis_change,
        }
    )
    out.attrs["window_bars"] = window
    out.attrs["missing_spot_bars"] = int(
        len(perp.frame) - len(merged)
    )
    return out


# ---------------------------------------------------------------------------
# 有效样本与 VIF
# ---------------------------------------------------------------------------
def newey_west_vif(series: pd.Series, max_lag: int) -> float:
    """特征自身的 Newey–West 方差膨胀因子（>=1）。"""
    values = pd.Series(series).replace([np.inf, -np.inf], np.nan).dropna()
    if values.shape[0] < 30:
        return float("nan")
    x = values.to_numpy(dtype=float)
    x = x - x.mean()
    denom = float((x * x).sum())
    if denom <= 0:
        return float("nan")
    correction = 1.0
    for lag in range(1, max_lag + 1):
        numerator = float((x[lag:] * x[:-lag]).sum())
        rho = numerator / denom
        correction += 2.0 * (1.0 - lag / (max_lag + 1.0)) * rho
    return float(max(1.0, correction))


# ---------------------------------------------------------------------------
# 单个 RankIC 端点
# ---------------------------------------------------------------------------
def _segment_mask(dataset: pd.DataFrame, window) -> pd.Series:
    start = pd.Timestamp(window[0], tz="UTC")
    end = pd.Timestamp(window[1], tz="UTC")
    return (dataset["open_time"] >= start) & (dataset["open_time"] < end)


def build_dataset(timeframe: str, horizon: int, window_days: int) -> pd.DataFrame:
    perp = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        datadir=DATA_DIR,
        start=START,
        end=END,
        data_format="feather",
    )
    features = pd.DataFrame(
        {
            "mom4": momentum(perp, periods=4),
            "mom12": momentum(perp, periods=12),
            "vol24": realized_volatility(perp, periods=24, annualize=False),
        },
        index=perp.frame.index,
    )
    dataset = build_research_dataset(
        market=perp, features=features, horizon=horizon
    ).reset_index(drop=True)

    basis = basis_frame(timeframe, window_days, START, END)
    dataset = dataset.merge(basis, on="open_time", how="left")
    dataset.attrs["window_bars"] = basis.attrs["window_bars"]
    dataset.attrs["missing_spot_bars"] = basis.attrs["missing_spot_bars"]
    return dataset


def rank_ic_endpoint(
    dataset: pd.DataFrame,
    column: str,
    setting: dict,
    *,
    n_boot: int,
    random_state: int,
) -> dict:
    horizon = setting["horizon"]
    test_mask = _segment_mask(dataset, TEST)
    valid = (
        test_mask
        & dataset[column].notna()
        & dataset["reference_return"].notna()
    )
    test = dataset.loc[valid]

    block = max(horizon, 1)
    boot = bootstrap_rank_ic(
        test[column],
        test["reference_return"],
        horizon=horizon,
        block_size=block,
        n_boot=n_boot,
        random_state=random_state,
    )

    monthly = []
    for month, group in test.groupby(test["open_time"].dt.strftime("%Y-%m")):
        if len(group) < 30:
            continue
        value = spearman_rank_ic(group[column], group["reference_return"])
        if np.isfinite(value):
            monthly.append({"month": str(month), "rank_ic": float(value)})
    if monthly:
        values = np.asarray([row["rank_ic"] for row in monthly], dtype=float)
        mean_monthly = float(values.mean())
        negative_ratio = float((values < 0).mean())
    else:
        mean_monthly = float("nan")
        negative_ratio = None

    vif = newey_west_vif(dataset.loc[test_mask, column], max_lag=1)
    n = int(test.shape[0])
    n_eff_autocorr = n / max(horizon, vif if np.isfinite(vif) else horizon)
    n_eff_design = n / horizon

    robustness = {}
    for multiplier in BLOCK_MULTIPLIERS:
        requested = block * multiplier
        if requested > n:
            continue
        robustness[str(multiplier)] = bootstrap_rank_ic(
            test[column],
            test["reference_return"],
            horizon=horizon,
            block_size=requested,
            n_boot=max(120, n_boot // 5),
            random_state=random_state,
        )

    evidence = rank_ic_sign_evidence(
        mean_monthly if np.isfinite(mean_monthly) else boot["point"],
        negative_ratio,
        len(monthly),
        n_eff=float(max(len(monthly), 3)),
    )

    return {
        "column": column,
        "n": n,
        "block_size": boot["block_size"],
        "rank_ic": boot,
        "mde_rank_ic_bootstrap": float(
            minimum_detectable_effect(boot["boot_sd"])
        ),
        "n_eff_design": float(n_eff_design),
        "n_eff_autocorr": float(n_eff_autocorr),
        "variance_inflation": float(vif),
        "monthly_mean_rank_ic": mean_monthly,
        "monthly_negative_ratio": negative_ratio,
        "monthly_months": len(monthly),
        "monthly": monthly,
        "mechanism_evidence": evidence,
        "block_sensitivity": robustness,
    }


def residualize(
    dataset: pd.DataFrame, column: str, *, base_columns: list[str]
) -> pd.Series:
    """在**训练段**拟合 OLS 并输出全样本残差。"""
    train_mask = _segment_mask(dataset, TRAIN)
    train = dataset.loc[
        train_mask & dataset[[column] + base_columns].notna().all(axis=1)
    ]
    if train.shape[0] < 100:
        raise ValueError("not enough training rows for residualization")

    means = {name: float(train[name].mean()) for name in base_columns}
    stds = {name: float(train[name].std(ddof=0)) for name in base_columns}
    stds = {name: (value if value > 0 else 1.0) for name, value in stds.items()}

    def design(frame):
        matrix = np.ones((frame.shape[0], len(base_columns) + 1))
        for position, name in enumerate(base_columns, start=1):
            matrix[:, position] = (
                frame[name].to_numpy(dtype=float) - means[name]
            ) / stds[name]
        return matrix

    def target(frame):
        return frame[column].to_numpy(dtype=float)

    train_design = design(train)
    coefficients, *_ = np.linalg.lstsq(
        train_design, target(train), rcond=None
    )

    predicted = design(dataset) @ coefficients
    return pd.Series(
        target(dataset) - predicted, index=dataset.index, name=f"{column}_resid"
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_direction_b(*, n_boot: int = 1000, random_state: int = 42) -> dict:
    policy = _policy()
    results = []

    for setting in SETTINGS:
        dataset = build_dataset(
            setting["timeframe"], setting["horizon"], setting["window_days"]
        )
        endpoint = rank_ic_endpoint(
            dataset, "basis_dev", setting, n_boot=n_boot, random_state=random_state
        )
        endpoint["setting"] = setting
        endpoint["diagnostics"] = {
            "window_bars": dataset.attrs["window_bars"],
            "missing_spot_bars": dataset.attrs["missing_spot_bars"],
            "basis_level_rank_ic": spearman_rank_ic(
                dataset.loc[
                    _segment_mask(dataset, TEST)
                    & dataset["basis"].notna(),
                    "basis",
                ],
                dataset.loc[
                    _segment_mask(dataset, TEST)
                    & dataset["basis"].notna(),
                    "reference_return",
                ],
            ),
            "basis_change_rank_ic": spearman_rank_ic(
                dataset.loc[
                    _segment_mask(dataset, TEST)
                    & dataset["basis_change"].notna(),
                    "basis_change",
                ],
                dataset.loc[
                    _segment_mask(dataset, TEST)
                    & dataset["basis_change"].notna(),
                    "reference_return",
                ],
            ),
        }
        results.append(endpoint)

        if setting["id"] == "H-B1a":
            # 在主设定（5m/h12）上做残差化
            dataset["basis_dev_resid"] = residualize(
                dataset, "basis_dev", base_columns=["mom4", "mom12", "vol24"]
            )
            residual_endpoint = rank_ic_endpoint(
                dataset,
                "basis_dev_resid",
                setting,
                n_boot=n_boot,
                random_state=random_state,
            )
            residual_endpoint["setting"] = H_B4_BASE
            residual_endpoint["amplitude_ratio_vs_original"] = (
                abs(residual_endpoint["rank_ic"]["point"])
                / abs(endpoint["rank_ic"]["point"])
                if endpoint["rank_ic"]["point"]
                else None
            )
            residual_endpoint["diagnostics"] = {
                "window_bars": dataset.attrs["window_bars"],
                "missing_spot_bars": dataset.attrs["missing_spot_bars"],
                "base_columns": ["mom4", "mom12", "vol24"],
                "original_rank_ic": endpoint["rank_ic"]["point"],
            }
            results.append(residual_endpoint)

    payload = finalize(results, policy)
    payload["method_version"] = METHOD_VERSION
    payload["not_run"] = list(NOT_RUN)
    payload["splits"] = {"train": list(TRAIN), "test": list(TEST)}
    payload["data"] = {
        "perp": "BTC/USDT:USDT",
        "spot": "BTC/USDT",
        "range": [START, END],
        "sealed": "2025-01-01+",
    }
    payload["n_boot"] = n_boot
    payload["random_state"] = random_state
    return payload


def finalize(results: list[dict], policy: VerdictPolicy) -> dict:
    raw_p = np.asarray(
        [result["rank_ic"]["p_two_sided"] for result in results], dtype=float
    )
    adjusted = adjust_pvalues(raw_p)["benjamini_hochberg"]

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
        result["p_raw"] = float(raw_p[position])
        result["adjusted_p_within_direction"] = float(adjusted[position])
        result["mechanism_evidence"]["sign_test_p_adjusted"] = float(
            mechanism_adjusted[position]
        )

        assessment = MetricAssessment(
            metric="rank_ic",
            point=result["rank_ic"]["point"],
            ci_lower=result["rank_ic"]["ci_95_lower"],
            ci_upper=result["rank_ic"]["ci_95_upper"],
            mde=result["mde_rank_ic_bootstrap"],
            practical_threshold=RANK_IC_PRACTICAL_THRESHOLD,
            adjusted_p=float(adjusted[position]),
            p_two_sided=result["rank_ic"]["p_two_sided"],
        )
        incremental = classify_result(
            {"rank_ic": assessment},
            primary_metric="rank_ic",
            stability_ratio=result["monthly_negative_ratio"]
            if result["setting"]["preregistered_sign"] < 0
            else (
                None
                if result["monthly_negative_ratio"] is None
                else 1.0 - result["monthly_negative_ratio"]
            ),
            max_abs_spearman=None,
            policy=policy,
        )

        point = result["rank_ic"]["point"]
        direction_consistent = (
            int(np.sign(point)) == int(result["setting"]["preregistered_sign"])
            if np.isfinite(point)
            else None
        )
        mechanism = _mechanism_verdict(
            result["mechanism_evidence"], direction_consistent, policy
        )
        result["direction_consistent"] = direction_consistent
        result["verdict"] = incremental.as_dict()
        result["mechanism_verdict"] = mechanism
        result["overall_verdict"] = combine_verdicts(incremental, mechanism)

    return {
        "family_size": len(results),
        "frozen_family_size": 4,
        "policy": {
            "alpha": policy.alpha,
            "power": policy.power,
            "practical_thresholds": dict(policy.practical_thresholds),
            "rank_ic_threshold_declared_this_round": RANK_IC_PRACTICAL_THRESHOLD,
        },
        "results": results,
    }


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

    add("# 预注册实验执行报告 B：非 OHLCV 因子（基差）")
    add("")
    add(f"- 方法版本：`{payload['method_version']}`")
    add(
        f"- 数据：永续 {payload['data']['perp']}，现货 {payload['data']['spot']}，"
        f"区间 `{payload['data']['range'][0]}` ~ `{payload['data']['range'][1]}`"
        f"（{payload['data']['sealed']} 不读取）"
    )
    add(
        f"- 切分：训练 `{payload['splits']['train']}`；测试 `{payload['splits']['test']}`"
    )
    add(
        f"- RankIC bootstrap n_boot={payload['n_boot']}，"
        f"random_state={payload['random_state']}"
    )
    add(
        f"- 判定 policy：{payload['policy']}；"
        f"族 = 本方向 {payload['family_size']} 个检验"
        f"（冻结族规模 {payload['frozen_family_size']}，见下文保守性说明）"
    )
    add("")
    add("## 未运行的预注册检验")
    add("")
    add("| ID | 设定 | 原因 |")
    add("|---|---|---|")
    for item in payload["not_run"]:
        add(f"| {item['id']} | {item['setting']} | {item['reason']} |")
    add("")

    for result in payload["results"]:
        setting = result["setting"]
        add(f"## {setting['id']}：`{result['column']}`（{setting['role']}）")
        add("")
        add(
            f"- 设定：{setting['timeframe']} / h={setting['horizon']} / "
            f"W={setting['window_days']}d（{result.get('diagnostics', {}).get('window_bars', '—')} 根）；"
            f"预注册符号 {setting['preregistered_sign']:+d}"
        )
        add(
            f"- n_test={result['n']}，块长={result['block_size']}，"
            f"VIF={_fmt(result['variance_inflation'], 2)}，"
            f"n_eff(design)={_fmt(result['n_eff_design'], 0)}，"
            f"n_eff(autocorr)={_fmt(result['n_eff_autocorr'], 0)}"
        )
        add(
            f"- RankIC={_fmt(result['rank_ic']['point'], 4)}，"
            f"95%CI [{_fmt(result['rank_ic']['ci_95_lower'], 4)}, "
            f"{_fmt(result['rank_ic']['ci_95_upper'], 4)}]，"
            f"p={_fmt(result['rank_ic']['p_two_sided'], 4)}，"
            f"BH p={_fmt(result['adjusted_p_within_direction'], 4)}，"
            f"MDE(bootstrap)={_fmt(result['mde_rank_ic_bootstrap'], 4)}"
        )
        add(
            f"- 逐月 RankIC 均值={_fmt(result['monthly_mean_rank_ic'], 4)}，"
            f"逐月负比={_fmt(result['monthly_negative_ratio'], 2)}"
            f"（{result['monthly_months']} 个月）"
        )
        if "amplitude_ratio_vs_original" in result:
            add(
                f"- 残差化振幅比（|残差 RankIC| / |原始 RankIC|）："
                f"{_fmt(result['amplitude_ratio_vs_original'], 2)}"
                f"（基线 {'+'.join(result['diagnostics']['base_columns'])}）"
            )
        diagnostics = result.get("diagnostics") or {}
        if "basis_level_rank_ic" in diagnostics:
            add(
                f"- 诊断：基差**水平项** RankIC={_fmt(diagnostics['basis_level_rank_ic'], 4)}，"
                f"`basis_change` RankIC={_fmt(diagnostics['basis_change_rank_ic'], 4)}"
            )
        add("")
        add("块长敏感性（RankIC）：")
        add("")
        add("| 倍数 | 块长 | RankIC | 95%CI |")
        add("|---|---|---|---|")
        for key, item in sorted(
            result["block_sensitivity"].items(), key=lambda kv: int(kv[0])
        ):
            add(
                f"| ×{key} | {item['block_size']} | {_fmt(item['point'], 4)} | "
                f"[{_fmt(item['ci_95_lower'], 4)}, {_fmt(item['ci_95_upper'], 4)}] |"
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
    setting = result["setting"]
    return {
        "run_id": run_id,
        "timeframe": setting["timeframe"],
        "horizon": setting["horizon"],
        "n": result["n"],
        "n_eff": {"design": result["n_eff_design"]},
        "block_size": result["block_size"],
        "bootstrap_samples": n_boot,
        "random_state": random_state,
        "primary_metric": "rank_ic",
        "metrics": {
            "rank_ic": {
                **result["rank_ic"],
                "mde": result["mde_rank_ic_bootstrap"],
                "practical_threshold": RANK_IC_PRACTICAL_THRESHOLD,
                "adjusted_p": result["adjusted_p_within_direction"],
            }
        },
        "verdict": result["verdict"],
        "mechanism_verdict": result["mechanism_verdict"],
        "overall_verdict": result["overall_verdict"],
    }


def record(payload: dict, *, random_state: int, n_boot: int) -> dict:
    """每个检验一条独立的 experiments 行 + 一条 statistical_reviews 行。

    理由同 Direction A：`statistical_reviews` 的幂等键是
    `(run_id, method_version, n_boot, random_state)`，共用 run_id 会导致
    多条评议互相覆盖。
    """
    run_ids = []
    review_ids = []
    decisions = []

    for result in payload["results"]:
        setting = result["setting"]
        run_id = ensure_analysis_run(
            RESEARCH_ROOT,
            analysis_id=f"prereg-b-{setting['id']}",
            config={
                "analysis": "prereg_direction_b_non_ohlcv",
                "method_version": METHOD_VERSION,
                "setting_id": setting["id"],
                "setting": setting,
                "column": result["column"],
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
                "analysis_id": f"prereg-b-{setting['id']}",
                "run_id": run_id,
                "hypothesis": (
                    f"{setting['id']}: {result['column']} 与未来收益负相关"
                    "（预注册符号 -1）"
                ),
                "conclusion": {
                    "overall_verdict": result["overall_verdict"]["label"],
                    "incremental_verdict": result["verdict"]["label"],
                    "incremental_reasons": list(result["verdict"]["reasons"]),
                    "mechanism_verdict": result["mechanism_verdict"]["label"],
                    "mechanism_reasons": list(
                        result["mechanism_verdict"]["reasons"]
                    ),
                    "rank_ic": result["rank_ic"]["point"],
                    "rank_ic_ci": [
                        result["rank_ic"]["ci_95_lower"],
                        result["rank_ic"]["ci_95_upper"],
                    ],
                    "mde_rank_ic": result["mde_rank_ic_bootstrap"],
                    "p_adjusted_within_direction": result[
                        "adjusted_p_within_direction"
                    ],
                    "amplitude_ratio_vs_original": result.get(
                        "amplitude_ratio_vs_original"
                    ),
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
    parser = argparse.ArgumentParser(prog="alpha_lab.non_ohlcv_cli")
    parser.add_argument("--n-boot", type=int, default=1000)
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
        path = Path(args.out_dir) / "prereg_b_results.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        info = record(
            payload,
            random_state=payload["random_state"],
            n_boot=payload["n_boot"],
        )
        print(f"recorded: {info}")
        return 0

    payload = run_direction_b(n_boot=args.n_boot, random_state=args.random_state)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "prereg_b_results.json"
    md_path = out_dir / "prereg_b_results.md"
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
            f"{result['setting']['id']} {result['column']}: "
            f"RankIC={result['rank_ic']['point']:.4f} "
            f"CI=[{result['rank_ic']['ci_95_lower']:.4f},"
            f"{result['rank_ic']['ci_95_upper']:.4f}] "
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
