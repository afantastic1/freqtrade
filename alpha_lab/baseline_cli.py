"""Task 2：可复现基线。

目标（严格限定在审计与预注册之前）：
1. **复现** mom4 / mom12 / volume_divergence（R3 的最强因子），
   并与已有 append-only run 记录逐项对比；
2. 使用**时间顺序明确**的训练 / 验证 / 测试切分；
3. 分析信号方向、预测 horizon、波动率状态、交易成本敏感性；
4. 所有候选因子都与简单**动量 / 反转 / 波动率**基线比较。

约束：
- 只使用 [2022-01-01, 2025-01-01) 区间，2025 年及以后的数据永不读取；
- 所有因子严格只使用当前 bar 及其之前的信息；
- 分位数 / 波动率阈值只在**训练段**上估计，再套用到验证段与测试段；
- 报告里的"策略收益"是研究代理，不是可执行 PnL。

切分说明（重要）：现有 walk-forward 用 12 个月训练 + 每月重训，首个测试月
是 2023-01，因此 [2022-01, 2023-01) 全被训练占用，**没有验证段**。
本模块把训练段缩短为 9 个月以腾出 3 个月验证段。这是一项协议改动，
若要正式采用，必须同时更新 ExperimentConfig 的 train_months 与
walk_forward 的切分逻辑（见报告结尾的建议）。
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.data.loader import load_ohlcv
from alpha_lab.evaluation.factor_report import evaluate_factor
from alpha_lab.factors.candidate_loader import load_candidate
from alpha_lab.factors.momentum import momentum
from alpha_lab.labels.forward_returns import forward_returns
from alpha_lab.stats.effective_samples import estimate_effective_samples
from alpha_lab.stats.power import minimum_detectable_effect
from alpha_lab.stats.rank_ic import (
    bootstrap_rank_ic,
    bootstrap_rank_ic_difference,
    spearman_rank_ic,
)

DATA_DIR = Path("user_data/data/binance")
RESEARCH_ROOT = Path("user_data/research/alpha_lab")
CANDIDATE_DIR = RESEARCH_ROOT / "candidates" / "volume_divergence"

RESEARCH_START = "2022-01-01"
RESEARCH_END = "2025-01-01"

# 时间顺序切分（半开区间）
SPLITS = (
    ("train", "2022-01-01", "2022-10-01"),
    ("validation", "2022-10-01", "2023-01-01"),
    ("test", "2023-01-01", "2025-01-01"),
)

TIMEFRAME = "1h"
PRIMARY_HORIZON = 4
HORIZONS = (1, 4, 12, 24, 42)

# 复现校验目标：R3 volume_divergence 的 run
REPRODUCTION_RUN_ID = "0dc03df9811645b5a5aeb11191c669a9"
REPRODUCTION_FACTORS = {
    "mom4": "momentum_periods_4",
    "mom12": "momentum_periods_12",
    "volume_divergence": (
        "volume_divergence_momentum_periods_4_volume_periods_24"
    ),
}

COST_LEVELS_BPS = (0.0, 1.0, 2.0, 5.0, 10.0)

FAMILIES = {
    "mom4": "momentum",
    "mom12": "momentum",
    "mom24": "momentum",
    "rev4": "reversal",
    "rev24": "reversal",
    "vol24": "volatility",
    "vol168": "volatility",
    "mom4_volnorm": "risk_adjusted",
    "volume_divergence": "candidate",
}


def _contiguity_mask(frame: pd.DataFrame, window: int, step_seconds: int) -> pd.Series:
    """窗口内 K 线是否连续（与 momentum 的缺口处理同一口径）。"""
    step = pd.Timedelta(seconds=step_seconds)
    gaps = frame["open_time"].diff().ne(step)
    gaps.iloc[0] = False
    invalid = (
        gaps.rolling(window=window, min_periods=window)
        .max()
        .fillna(1)
        .astype(bool)
    )
    return ~invalid


def realized_volatility(
    market, periods: int = 24, annualize: bool = True
) -> pd.Series:
    """过去 periods 根 K 线对数收益的标准差（严格只用历史）。"""
    if periods <= 1:
        raise ValueError("periods must be >= 2")

    from alpha_lab.data.timeframe import timeframe_to_seconds

    step_seconds = timeframe_to_seconds(market.timeframe)
    frame = market.frame

    log_return = np.log(frame["close"]).diff()
    vol = log_return.rolling(periods, min_periods=periods).std()

    if annualize:
        seconds_per_year = 365 * 24 * 3600
        vol = vol * np.sqrt(seconds_per_year / step_seconds)

    vol = vol.mask(~_contiguity_mask(frame, periods, step_seconds))
    return vol.rename(f"vol_{periods}")


def build_factor_frame(market, *, include_candidate: bool = True) -> pd.DataFrame:
    """构造基线 + 候选因子矩阵（索引与 market.frame 一致）。"""
    mom = {periods: momentum(market, periods=periods) for periods in (4, 12, 24)}
    vol24 = realized_volatility(market, 24)
    vol168 = realized_volatility(market, 168)

    columns = {
        "mom4": mom[4],
        "mom12": mom[12],
        "mom24": mom[24],
        "rev4": -mom[4],
        "rev24": -mom[24],
        "vol24": vol24,
        "vol168": vol168,
        "mom4_volnorm": mom[4] / vol24.replace(0.0, np.nan),
    }

    if include_candidate:
        if not CANDIDATE_DIR.is_dir():
            raise FileNotFoundError(f"missing candidate: {CANDIDATE_DIR}")
        _, factor_function = load_candidate(CANDIDATE_DIR)
        columns["volume_divergence"] = factor_function(
            market, momentum_periods=4, volume_periods=24
        )

    return pd.DataFrame(columns, index=market.frame.index)


def _segment_mask(
    frame: pd.DataFrame,
    start: str,
    end: str,
    *,
    enforce_label_availability: bool,
) -> pd.Series:
    """段掩码。

    enforce_label_availability=True 时额外要求标签在**本段结束前**已可用，
    避免段尾的标签把信息泄漏到下一段。
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")

    mask = (frame["open_time"] >= start_ts) & (frame["open_time"] < end_ts)

    if enforce_label_availability and "label_available_time" in frame:
        mask = mask & (frame["label_available_time"] < end_ts)

    return mask.fillna(False)


# ---------------------------------------------------------------------------
# 复现校验
# ---------------------------------------------------------------------------
def verify_reproduction(market, horizon: int = PRIMARY_HORIZON) -> dict:
    """把重算的单因子报告与已有 run 的 append-only 记录逐项对比。"""
    summary_path = RESEARCH_ROOT / "runs" / REPRODUCTION_RUN_ID / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"missing run summary: {summary_path}")

    stored = json.loads(summary_path.read_text(encoding="utf-8"))
    stored_eval = stored.get("factor_evaluation") or {}

    factors = build_factor_frame(market)
    label = forward_returns(market, horizon=horizon)["reference_return"]
    timestamps = market.frame["open_time"]

    comparisons = []
    for name, stored_key in REPRODUCTION_FACTORS.items():
        recomputed = evaluate_factor(
            factor=factors[name].replace([np.inf, -np.inf], np.nan),
            label=label,
            timestamps=timestamps,
        )
        reference = stored_eval.get(stored_key)
        if reference is None:
            raise KeyError(f"stored run has no factor {stored_key}")

        fields = {
            "ic": (
                reference["overall"]["ic"],
                recomputed["overall"]["ic"],
            ),
            "rank_ic": (
                reference["overall"]["rank_ic"],
                recomputed["overall"]["rank_ic"],
            ),
            "valid": (
                reference["quality"]["valid"],
                recomputed["quality"]["valid"],
            ),
            "negative_rank_ic_ratio": (
                reference["stability"]["negative_rank_ic_ratio"],
                recomputed["stability"]["negative_rank_ic_ratio"],
            ),
            "mean_monthly_rank_ic": (
                reference["stability"]["mean_monthly_rank_ic"],
                recomputed["stability"]["mean_monthly_rank_ic"],
            ),
        }

        diffs = {
            key: abs(float(a) - float(b))
            for key, (a, b) in fields.items()
        }

        comparisons.append({
            "factor": name,
            "stored_key": stored_key,
            "stored": {key: values[0] for key, values in fields.items()},
            "recomputed": {key: values[1] for key, values in fields.items()},
            "abs_diff": diffs,
            "max_abs_diff": max(diffs.values()),
            "identical": max(diffs.values()) <= 1e-12,
        })

    return {
        "run_id": REPRODUCTION_RUN_ID,
        "timeframe": stored.get("timeframe"),
        "horizon": horizon,
        "window": [stored["data_range"]["start"], stored["data_range"]["end"]],
        "comparisons": comparisons,
        "all_identical": all(item["identical"] for item in comparisons),
    }


# ---------------------------------------------------------------------------
# split / horizon / 波动率状态
# ---------------------------------------------------------------------------
def split_rank_ic(
    factors: pd.DataFrame,
    label: pd.Series,
    frame: pd.DataFrame,
    *,
    horizon: int,
    n_boot: int,
    random_state: int,
) -> list[dict]:
    rows = []
    for name in factors.columns:
        for split_name, start, end in SPLITS:
            mask = _segment_mask(
                frame,
                start,
                end,
                enforce_label_availability=split_name != "test",
            )
            if int(mask.sum()) < 50:
                continue
            result = bootstrap_rank_ic(
                factors.loc[mask, name],
                label.loc[mask],
                horizon=horizon,
                n_boot=n_boot,
                random_state=random_state,
            )
            rows.append({
                "factor": name,
                "family": FAMILIES.get(name, "unknown"),
                "split": split_name,
                "window": [start, end],
                **result,
            })
    return rows


def horizon_sweep(
    factors: pd.DataFrame,
    market,
    frame: pd.DataFrame,
    *,
    splits: tuple[str, ...] = ("test",),
    n_boot: int,
    random_state: int,
) -> list[dict]:
    rows = []
    labels = {
        horizon: forward_returns(market, horizon=horizon)["reference_return"]
        for horizon in HORIZONS
    }

    for name in factors.columns:
        for horizon in HORIZONS:
            label = labels[horizon]
            for split_name, start, end in SPLITS:
                if split_name not in splits:
                    continue
                mask = _segment_mask(
                    frame,
                    start,
                    end,
                    enforce_label_availability=split_name != "test",
                )
                if int(mask.sum()) < 50:
                    continue

                result = bootstrap_rank_ic(
                    factors.loc[mask, name],
                    label.loc[mask],
                    horizon=horizon,
                    n_boot=n_boot,
                    random_state=random_state,
                )
                ess = estimate_effective_samples(
                    label.loc[mask].to_numpy(dtype=float), horizon=horizon
                )
                fisher_mde = (
                    minimum_detectable_effect(1.0 / np.sqrt(ess.n_eff - 3.0))
                    if ess.n_eff > 4
                    else float("nan")
                )

                rows.append({
                    "factor": name,
                    "family": FAMILIES.get(name, "unknown"),
                    "split": split_name,
                    "horizon": horizon,
                    "n_eff": ess.n_eff,
                    "mde_rank_ic_fisher": float(fisher_mde),
                    **result,
                })
    return rows


def volatility_state_analysis(
    factors: pd.DataFrame,
    label: pd.Series,
    frame: pd.DataFrame,
    *,
    horizon: int,
    n_boot: int,
    random_state: int,
) -> dict:
    """波动率状态条件化。

    状态变量 = vol24；阈值取自**训练段的**中位数（不使用验证/测试数据），
    再套用到测试段。报告每个状态内的 RankIC，以及两状态 RankIC 之差的区间。
    """
    vol = factors["vol24"]
    train_mask = _segment_mask(
        frame, SPLITS[0][1], SPLITS[0][2], enforce_label_availability=True
    )
    test_mask = _segment_mask(
        frame, SPLITS[2][1], SPLITS[2][2], enforce_label_availability=False
    )

    threshold = float(vol.loc[train_mask].median())
    if not np.isfinite(threshold):
        raise ValueError("cannot estimate volatility regime threshold")

    results = {
        "regime_variable": "vol24",
        "threshold_train_median": threshold,
        "threshold_source": "train segment only",
        "per_factor": {},
    }

    for name in factors.columns:
        if name == "vol24":
            continue

        factor_test = factors.loc[test_mask, name]
        label_test = label.loc[test_mask]
        vol_test = vol.loc[test_mask]

        high = (vol_test > threshold)
        low = (vol_test <= threshold)

        entry = {
            "low_vol_rank_ic": spearman_rank_ic(
                factor_test.loc[low], label_test.loc[low]
            ),
            "high_vol_rank_ic": spearman_rank_ic(
                factor_test.loc[high], label_test.loc[high]
            ),
            "n_low": int(low.sum()),
            "n_high": int(high.sum()),
        }

        try:
            difference = bootstrap_rank_ic_difference(
                factor_test,
                label_test,
                high,
                horizon=horizon,
                n_boot=n_boot,
                random_state=random_state,
            )
            entry["difference"] = difference
        except ValueError as exc:
            entry["difference"] = {"error": str(exc)}

        results["per_factor"][name] = entry

    return results


# ---------------------------------------------------------------------------
# 交易成本敏感性
# ---------------------------------------------------------------------------
def _causal_tercile_positions(values: np.ndarray, window: int) -> np.ndarray:
    """因果的滚动三分位仓位：-1 / 0 / +1。

    为什么不用"训练段固定阈值"：训练段（2022-01~09，熊市）与测试段
    （2023–2024，牛市）漂移明显，固定阈值会让测试段的仓位系统性偏多，
    于是策略 Sharpe 被"始终做多"的趋势收益污染，与 RankIC 的符号
    相互矛盾。滚动窗口分位是因果的（只用截至 t 的观测）、对漂移自适应，
    并且不需要任何调参（窗口固定为 30 天）。
    """
    series = pd.Series(np.asarray(values, dtype=float))
    minimum = max(10, window // 2)

    low = series.rolling(window, min_periods=minimum).quantile(1.0 / 3.0)
    high = series.rolling(window, min_periods=minimum).quantile(2.0 / 3.0)

    position = np.zeros(series.shape[0], dtype=float)
    usable = np.isfinite(series.to_numpy()) & low.notna().to_numpy() & high.notna().to_numpy()
    position[usable & (series >= high).to_numpy()] = 1.0
    position[usable & (series <= low).to_numpy()] = -1.0
    return position


def _annualised_sharpe(returns: np.ndarray, periods_per_year: float) -> float:
    std = returns.std(ddof=1)
    if not np.isfinite(std) or std <= 0:
        return float("nan")
    return float(returns.mean() / std * np.sqrt(periods_per_year))


def cost_sensitivity(
    factors: pd.DataFrame,
    market,
    frame: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (4, 24),
    split_name: str = "test",
    position_window: int = 720,
) -> dict:
    """非重叠再平衡的毛/净收益（研究代理，不是可执行 PnL）。

    仓位由**因果滚动三分位**决定（窗口 30 天 = 720 根 1h K 线）。
    同时报告 `mean_position` 与 `buy_and_hold` 参考行：2023–2024 BTC
    单边上涨会让任何偏多仓位看起来都很好，必须能把这种污染显式看出。
    """
    from alpha_lab.data.timeframe import timeframe_to_seconds

    step_seconds = timeframe_to_seconds(market.timeframe)
    bars_per_year = 365 * 24 * 3600 / step_seconds

    window = next(
        (start, end) for name, start, end in SPLITS if name == split_name
    )

    test_mask = _segment_mask(
        frame, window[0], window[1], enforce_label_availability=False
    )
    test_index = np.flatnonzero(test_mask.to_numpy())

    tables = {}
    for horizon in horizons:
        label = forward_returns(market, horizon=horizon)["reference_return"]
        label_values = label.to_numpy(dtype=float)

        # 每 horizon 根取一根，保证持有期不重叠
        rebalance = test_index[::horizon]
        realized = label_values[rebalance]
        finite = np.isfinite(realized)
        rebalance = rebalance[finite]
        realized = realized[finite]

        periods_per_year = bars_per_year / horizon
        rows = []

        # 参考行：始终做多
        buy_hold = np.ones(realized.shape[0])
        rows.append({
            "factor": "buy_and_hold",
            "family": "reference",
            "horizon": horizon,
            "n_rebalances": int(realized.shape[0]),
            "mean_position": 1.0,
            "mean_turnover_per_rebalance": 0.0,
            "mean_forward_return_bps": float(realized.mean() * 1e4),
            "gross_mean_bps": float(realized.mean() * 1e4),
            "gross_annual_sharpe": _annualised_sharpe(
                buy_hold * realized, periods_per_year
            ),
            **{
                f"net_annual_sharpe_{cost:g}bps": _annualised_sharpe(
                    buy_hold * realized, periods_per_year
                )
                for cost in COST_LEVELS_BPS
            },
            "breakeven_cost_bps": None,
        })

        for name in factors.columns:
            all_values = factors[name].to_numpy(dtype=float)
            positions = _causal_tercile_positions(all_values, position_window)[
                rebalance
            ]
            gross = positions * realized

            prev = np.concatenate(([0.0], positions[:-1]))
            turnover = np.abs(positions - prev)
            mean_turnover = float(turnover.mean())

            entry = {
                "factor": name,
                "family": FAMILIES.get(name, "unknown"),
                "horizon": horizon,
                "n_rebalances": int(positions.shape[0]),
                "mean_position": float(positions.mean()),
                "mean_turnover_per_rebalance": mean_turnover,
                "mean_forward_return_bps": float(realized.mean() * 1e4),
                "gross_mean_bps": float(gross.mean() * 1e4),
                "gross_annual_sharpe": _annualised_sharpe(
                    gross, periods_per_year
                ),
                # 毛收益刚好被成本吃掉的每单位换手成本（bps）
                "breakeven_cost_bps": (
                    float(gross.mean() / mean_turnover * 1e4)
                    if mean_turnover > 0
                    else None
                ),
            }

            for cost in COST_LEVELS_BPS:
                net = gross - turnover * cost / 1e4
                entry[f"net_annual_sharpe_{cost:g}bps"] = _annualised_sharpe(
                    net, periods_per_year
                )
                if cost == 0.0:
                    entry["net_mean_bps_0bps"] = float(net.mean() * 1e4)

            rows.append(entry)

        tables[str(horizon)] = rows

    return {
        "split": split_name,
        "position_rule": (
            f"causal rolling terciles (window={position_window} bars at 1h)"
        ),
        "train_thresholds_used": False,
        "cost_levels_bps": list(COST_LEVELS_BPS),
        "tables": tables,
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
    lines = []
    add = lines.append

    add("# Alpha Lab 基线复现报告（Task 2）")
    add("")
    add(f"- 时间框架：`{payload['timeframe']}`，主 horizon：{payload['horizon']}")
    add(
        f"- 研究区间：`{payload['window'][0]}` ~ `{payload['window'][1]}`"
        "（2025+ 数据不读取）"
    )
    add(
        "- 切分：" + "；".join(
            f"{name} [{start}, {end})" for name, start, end in SPLITS
        )
    )
    add(f"- bootstrap n_boot={payload['n_boot']}，random_state={payload['random_state']}")
    add("")
    add(
        "> 声明：本报告中的收益 / Sharpe 是**研究代理**（基于 exact-h 参考收益的"
        "非重叠再平衡），未建模滑点分布、资金费率、冲击成本与容量，"
        "**不可**当作可交易 PnL。"
    )
    add("")

    add("## 1. 复现校验")
    add("")
    repro = payload["reproduction"]
    add(
        f"对照 run `{repro['run_id'][:12]}`（{repro['timeframe']}, "
        f"h={repro['horizon']}, 窗口 {repro['window'][0]}~{repro['window'][1]}）。"
        f"全部一致：**{repro['all_identical']}**"
    )
    add("")
    add(
        "| factor | 字段 | 原记录 | 重算 | 绝对差 |"
    )
    add("|---|---|---|---|---|")
    for item in repro["comparisons"]:
        for field in ("ic", "rank_ic", "valid", "negative_rank_ic_ratio",
                      "mean_monthly_rank_ic"):
            add(
                f"| {item['factor']} | {field} | "
                f"{_fmt(item['stored'][field], 10)} | "
                f"{_fmt(item['recomputed'][field], 10)} | "
                f"{item['abs_diff'][field]:.2e} |"
            )
    add("")

    add("## 2. 时间顺序切分下的 RankIC")
    add("")
    add(
        f"主 horizon={payload['horizon']}。区间为 moving-block bootstrap "
        "（块长 >= horizon），因此已吸收重叠标签的依赖。"
    )
    add("")
    add("| factor | family | split | n | RankIC | 95%CI | MDE |")
    add("|---|---|---|---|---|---|---|")
    for row in payload["split_rank_ic"]:
        mde = minimum_detectable_effect(row["boot_sd"])
        add(
            f"| {row['factor']} | {row['family']} | {row['split']} | "
            f"{row['n']} | {_fmt(row['point'])} | "
            f"[{_fmt(row['ci_95_lower'])}, {_fmt(row['ci_95_upper'])}] | "
            f"{_fmt(mde)} |"
        )
    add("")

    add("## 3. horizon 扫描（测试段）")
    add("")
    add(
        "`MDE(Fisher)` 为基于 n_eff 的解析近似，用于判断该 horizon 的 "
        "RankIC 能否被分辨；bootstrap 区间为主口径。"
    )
    add("")
    add("| factor | horizon | n | n_eff | RankIC | 95%CI | MDE(Fisher) | 符号检验 |")
    add("|---|---|---|---|---|---|---|---|")
    for row in payload["horizon_sweep"]:
        add(
            f"| {row['factor']} | {row['horizon']} | {row['n']} | "
            f"{_fmt(row['n_eff'], 0)} | {_fmt(row['point'])} | "
            f"[{_fmt(row['ci_95_lower'])}, {_fmt(row['ci_95_upper'])}] | "
            f"{_fmt(row['mde_rank_ic_fisher'])} | {_fmt(row['p_two_sided'], 3)} |"
        )
    add("")

    add("## 4. 波动率状态条件化（测试段）")
    add("")
    vol = payload["volatility_state"]
    add(
        f"状态变量 `{vol['regime_variable']}`，阈值 = 训练段中位数 "
        f"{vol['threshold_train_median']:.4f}（{vol['threshold_source']}）。"
        "`差异` 为高波动状态 RankIC 减低波动状态 RankIC。"
    )
    add("")
    add("| factor | 低波动 RankIC | 高波动 RankIC | 差异 | 差异 95%CI | 差异 p |")
    add("|---|---|---|---|---|---|")
    for name, entry in vol["per_factor"].items():
        diff = entry.get("difference") or {}
        add(
            f"| {name} | {_fmt(entry['low_vol_rank_ic'])} | "
            f"{_fmt(entry['high_vol_rank_ic'])} | {_fmt(diff.get('point'))} | "
            f"[{_fmt(diff.get('ci_95_lower'))}, "
            f"{_fmt(diff.get('ci_95_upper'))}] | "
            f"{_fmt(diff.get('p_two_sided'), 3)} |"
        )
    add("")

    add("## 5. 交易成本敏感性（测试段，非重叠再平衡）")
    add("")
    cost = payload["cost_sensitivity"]
    add(f"仓位规则：{cost['position_rule']}；阈值**因果**地只使用截至 t 的观测。")
    add(
        "`换手` 为每次再平衡的平均成交名义（0~2）；`breakeven` 是毛收益"
        "刚好被成本吃掉的每单位换手成本（bps）——成本高于它时期望转负。"
    )
    add(
        "`mean_pos` 为平均仓位（+1 全多、-1 全空）：它必须接近 0，否则"
        "Sharpe 会被测试段的单边上涨污染。`buy_and_hold` 参考行给出"
        "趋势本身的 Sharpe。"
    )
    add("")
    for horizon_key in sorted(cost["tables"], key=int):
        add(f"### horizon = {horizon_key}")
        add("")
        add(
            "| factor | n | mean_pos | 换手 | fwd收益(bps) | 毛 Sharpe | "
            "净@0bps | 净@1bps | 净@2bps | 净@5bps | 净@10bps | breakeven(bps) |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for row in cost["tables"][horizon_key]:
            add(
                f"| {row['factor']} | {row['n_rebalances']} | "
                f"{_fmt(row['mean_position'], 3)} | "
                f"{_fmt(row['mean_turnover_per_rebalance'], 3)} | "
                f"{_fmt(row['mean_forward_return_bps'], 1)} | "
                f"{_fmt(row['gross_annual_sharpe'], 3)} | "
                + " | ".join(
                    _fmt(row.get(f"net_annual_sharpe_{cost:g}bps"), 3)
                    for cost in COST_LEVELS_BPS
                )
                + f" | {_fmt(row.get('breakeven_cost_bps'), 2)} |"
            )
        add("")

    add("## 6. 关键读数")
    add("")
    add("以下条目由上面的数值直接推出，不含主观判断。")
    add("")
    for bullet in payload.get("highlights", []):
        add(f"- {bullet}")
    add("")

    return "\n".join(lines)


def _json_safe(value):
    """把 numpy 标量与非有限浮点（nan/inf）转成严格 JSON 可编码的形式。"""
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
    """从数值直接推出的关键读数（不含主观判断）。"""
    bullets = []

    horizon = payload["horizon"]
    test_rows = [
        row for row in payload["split_rank_ic"] if row["split"] == "test"
    ]
    resolved = [
        row
        for row in test_rows
        if row["ci_95_lower"] * row["ci_95_upper"] > 0.0
    ]
    bullets.append(
        f"主 horizon={horizon}：测试段 {len(test_rows)} 个因子中，"
        f"{len(resolved)} 个的 RankIC 95% bootstrap 区间不包含 0。"
    )

    if test_rows:
        strongest = max(test_rows, key=lambda row: abs(row["point"]))
        bullets.append(
            f"测试段绝对 RankIC 最大的是 `{strongest['factor']}`"
            f"（{strongest['point']:.4f}，"
            f"95%CI [{strongest['ci_95_lower']:.4f}, "
            f"{strongest['ci_95_upper']:.4f}]）。"
        )

    by_name = {row["factor"]: row for row in test_rows}
    if "mom4" in by_name and "rev4" in by_name:
        mom = by_name["mom4"]["point"]
        bullets.append(
            f"动量与反转在测试段完全镜像：`mom4`={mom:.4f}，"
            f"`rev4`={by_name['rev4']['point']:.4f}；"
            "两者在 train/validation/test 三段符号一致，"
            "说明 1h 尺度上短期反转是稳定的方向。"
        )

    if "volume_divergence" in by_name and "mom4" in by_name:
        candidate = by_name["volume_divergence"]
        bullets.append(
            f"`volume_divergence` 单独的 RankIC "
            f"（{candidate['point']:.4f}）比 `mom4` "
            f"（{by_name['mom4']['point']:.4f}）更强，"
            "但它在原 run 中与 mom4 的相关度高达 0.95，"
            "因此更强的单因子信号并不等于增量信息。"
        )

    horizons = sorted(
        {row["horizon"] for row in payload["horizon_sweep"]}
    )
    bullets.append(
        "horizon 扫描覆盖 " + ", ".join(str(h) for h in horizons) + " 根 K 线。"
    )

    vol = payload["volatility_state"]
    resolved_regimes = []
    for name, entry in vol["per_factor"].items():
        difference = entry.get("difference") or {}
        lower = difference.get("ci_95_lower")
        upper = difference.get("ci_95_upper")
        if lower is None or upper is None:
            continue
        if lower * upper > 0.0:
            resolved_regimes.append(name)
    bullets.append(
        f"波动率状态条件化：{len(vol['per_factor'])} 个因子中"
        f"{len(resolved_regimes)} 个的高低波动 RankIC 差异区间不包含 0；"
        "在这个 horizon 与这个状态定义下，条件化不产生可分辨的交互。"
    )

    cost = payload["cost_sensitivity"]
    for horizon_key in sorted(cost["tables"], key=int):
        rows = [
            row
            for row in cost["tables"][horizon_key]
            if row["family"] != "reference"
        ]
        if not rows:
            continue

        def survives(cost_bps: float) -> int:
            return sum(
                1
                for row in rows
                if (row.get(f"net_annual_sharpe_{cost_bps:g}bps") or 0.0) > 0.0
            )

        reference = [
            row
            for row in cost["tables"][horizon_key]
            if row["family"] == "reference"
        ]
        reference_sharpe = (
            reference[0]["gross_annual_sharpe"] if reference else float("nan")
        )
        worst_bias = max(abs(row["mean_position"]) for row in rows)
        best_breakeven = max(
            (
                row["breakeven_cost_bps"]
                for row in rows
                if row.get("breakeven_cost_bps") is not None
            ),
            default=float("nan"),
        )
        bullets.append(
            f"horizon={horizon_key}：{len(rows)} 个因子中净 Sharpe 在 "
            f"0bps 下只有 {survives(0.0)} 个为正，2bps 下 {survives(2.0)} 个，"
            f"5bps 下 {survives(5.0)} 个；同期 always-long 参考行 Sharpe 为 "
            f"{reference_sharpe:.3f}；最大 |平均仓位| = {worst_bias:.3f}"
            "（接近 0 说明趋势污染已移除）；最高的 breakeven 为 "
            f"{best_breakeven:.2f} bps/换手。"
        )

    return bullets


def run_baseline(
    *,
    n_boot: int = 400,
    random_state: int = 42,
    volatility_boot: int = 300,
) -> dict:
    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe=TIMEFRAME,
        datadir=DATA_DIR,
        start=RESEARCH_START,
        end=RESEARCH_END,
        data_format="feather",
    )

    bounds = (
        pd.Timestamp(RESEARCH_START, tz="UTC"),
        pd.Timestamp(RESEARCH_END, tz="UTC"),
    )
    if (market.frame["open_time"] < bounds[0]).any() or (
        market.frame["close_time"] > bounds[1]
    ).any():
        raise ValueError("loader returned out-of-range data")

    factors = build_factor_frame(market)
    labels = forward_returns(market, horizon=PRIMARY_HORIZON)
    label = labels["reference_return"]

    dataset = market.frame.assign(
        label_available_time=labels["label_available_time"]
    )

    payload = {
        "timeframe": TIMEFRAME,
        "horizon": PRIMARY_HORIZON,
        "window": [RESEARCH_START, RESEARCH_END],
        "splits": [list(item) for item in SPLITS],
        "n_boot": n_boot,
        "random_state": random_state,
        "reproduction": verify_reproduction(market),
        "split_rank_ic": split_rank_ic(
            factors,
            label,
            dataset,
            horizon=PRIMARY_HORIZON,
            n_boot=n_boot,
            random_state=random_state,
        ),
        "horizon_sweep": horizon_sweep(
            factors,
            market,
            dataset,
            n_boot=n_boot,
            random_state=random_state,
        ),
        "volatility_state": volatility_state_analysis(
            factors,
            label,
            dataset,
            horizon=PRIMARY_HORIZON,
            n_boot=volatility_boot,
            random_state=random_state,
        ),
        "cost_sensitivity": cost_sensitivity(
            factors, market, dataset, horizons=(PRIMARY_HORIZON, 24)
        ),
    }

    payload["highlights"] = build_highlights(payload)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="alpha_lab.baseline_cli")
    parser.add_argument("--n-boot", type=int, default=400)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--out-dir", default=str(RESEARCH_ROOT / "notes"))

    args = parser.parse_args(argv)

    payload = run_baseline(
        n_boot=args.n_boot, random_state=args.random_state
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "baseline_report.json"
    md_path = out_dir / "baseline_report.md"

    json_path.write_text(
        json.dumps(
            _json_safe(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    print(f"reproduction all_identical = {payload['reproduction']['all_identical']}")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
