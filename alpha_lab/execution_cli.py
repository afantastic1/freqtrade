"""真实成本下的净收益与换手缩减经济学（本轮执行报告 3）。

回答的问题：

1. 冻结的 `ref_combo = 0.5·rank(rev4) + 0.5·rank(vol24)`（以及低换手变体
   `+vol168`）在**真实费用 / 滑点 / 资金费率 / 仓位约束**下的净收益；
2. **换手缩减**本身的经济价值有多大（三种机制：逐根重平衡、每 h 根重平衡、
   滞回死区）；
3. 与 `always_long`、`always_flat`、以及单因子基线相比如何。

实现口径（全部因果、无前视）：

- 信号在 bar t 收盘时可用，仓位在 **open[t+1] 成交**（即 `p_exec = p_signal.shift(1)`）；
- bar 级收益 `r_t = open[t+1]/open[t] − 1`；
- 交易成本 = `|Δp| × (费用 + 滑点)`，费用按**单边**计；
- 资金费率：在 00/08/16 UTC 结算时按持仓 `p_t × funding_rate` 计（正费率时多头付出）；
- 仓位约束：`|p| ≤ 1`（无杠杆）；
- 所有分位 / 排名阈值都用**因果滚动窗口**，不用训练段固定阈值。

这是一项**研究代理**，不是可执行 PnL。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_lab.baseline_cli import (
    _annualised_sharpe,
    _causal_tercile_positions,
    realized_volatility,
)
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.factors.momentum import momentum
from alpha_lab.prereg_record import append_decisions

DATA_DIR = Path("user_data/data/binance")
RESEARCH_ROOT = Path("user_data/research/alpha_lab")
NOTES = RESEARCH_ROOT / "notes"

START = "2022-01-01"
END = "2025-01-01"
TEST = ("2023-01-01", "2025-01-01")

METHOD_VERSION = "execution-v1"
DECISION_LOG = "prereg_decisions.jsonl"

COST_LEVELS_BPS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0)
HORIZONS = (4, 24)
MECHANISMS = ("naive", "hold_h", "hysteresis")
RANK_WINDOW = 720  # 30 天 @ 1h
MAX_GROSS = 1.0
HYSTERESIS_ENTRY = 0.60
HYSTERESIS_EXIT = 0.50

FAMILIES = {
    "rev4": "reversal",
    "mom4": "momentum",
    "mom24": "momentum",
    "vol24": "volatility",
    "vol168": "volatility",
    "ref_combo": "composite (frozen)",
    "ref_combo_lowturnover": "composite (frozen, +vol168)",
}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def causal_rank(series: pd.Series, window: int = RANK_WINDOW) -> pd.Series:
    """因果滚动百分位排名（只用截至 t 的窗口）。"""
    values = pd.Series(series, dtype=float).replace([np.inf, -np.inf], np.nan)
    minimum = max(20, window // 4)
    return values.rolling(window, min_periods=minimum).rank(pct=True)


def hold_positions(positions: np.ndarray, horizon: int) -> np.ndarray:
    """每 horizon 根 K 线才更新一次目标仓位，其余时间保持不变。"""
    out = np.empty_like(positions)
    state = 0.0
    for index, value in enumerate(positions):
        if index % horizon == 0:
            state = value
        out[index] = state
    return out


def hysteresis_positions(
    rank: pd.Series,
    *,
    entry: float = HYSTERESIS_ENTRY,
    exit_level: float = HYSTERESIS_EXIT,
) -> np.ndarray:
    """滞回死区仓位：只在排名越过 entry 时开仓，越过 exit 时平仓。"""
    values = rank.to_numpy(dtype=float)
    out = np.zeros(values.shape[0], dtype=float)
    state = 0.0
    for index, value in enumerate(values):
        if np.isfinite(value):
            if state == 0.0:
                if value >= entry:
                    state = 1.0
                elif value <= 1.0 - entry:
                    state = -1.0
            elif state == 1.0:
                if value <= exit_level:
                    state = 0.0
            elif state == -1.0:
                if value >= 1.0 - exit_level:
                    state = 0.0
        out[index] = state
    return out


def build_positions(
    signal: np.ndarray,
    rank: pd.Series,
    mechanism: str,
    horizon: int,
    window: int = RANK_WINDOW,
) -> np.ndarray:
    naive = _causal_tercile_positions(signal, window)
    if mechanism == "naive":
        return naive
    if mechanism == "hold_h":
        return hold_positions(naive, horizon)
    if mechanism == "hysteresis":
        return hysteresis_positions(rank)
    raise ValueError(f"unknown mechanism: {mechanism}")


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
def load_execution_frame():
    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        datadir=DATA_DIR,
        start=START,
        end=END,
        data_format="feather",
    )
    frame = market.frame[["open_time", "open", "close"]].reset_index(drop=True)

    rev4 = -momentum(market, periods=4)
    mom4 = momentum(market, periods=4)
    mom24 = momentum(market, periods=24)
    vol24 = realized_volatility(market, periods=24, annualize=False)
    vol168 = realized_volatility(market, periods=168, annualize=False)

    signals = pd.DataFrame(
        {
            "rev4": rev4,
            "mom4": mom4,
            "mom24": mom24,
            "vol24": vol24,
            "vol168": vol168,
        }
    )
    ranks = {name: causal_rank(signals[name]) for name in signals.columns}
    # 组合信号必须由**因果**排名构成：用全样本 rank 会引入前视。
    ranks["ref_combo"] = 0.5 * ranks["rev4"] + 0.5 * ranks["vol24"]
    ranks["ref_combo_lowturnover"] = (
        0.5 * ranks["rev4"] + 0.5 * ranks["vol168"]
    )
    signals["ref_combo"] = ranks["ref_combo"]
    signals["ref_combo_lowturnover"] = ranks["ref_combo_lowturnover"]

    # 资金费率（稀疏：仅 00/08/16 UTC）
    funding = _load_funding(frame["open_time"])

    returns = frame["open"].shift(-1) / frame["open"] - 1.0
    return {
        "market": market,
        "frame": frame,
        "signals": signals,
        "ranks": ranks,
        "funding": funding,
        "returns": returns,
    }


def _load_funding(open_time: pd.Series) -> np.ndarray:
    from freqtrade.configuration.timerange import TimeRange
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType

    start_ts = pd.Timestamp(START, tz="UTC")
    end_ts = pd.Timestamp(END, tz="UTC")
    history = load_pair_history(
        pair="BTC/USDT:USDT",
        timeframe="1h",
        datadir=DATA_DIR,
        timerange=TimeRange(
            starttype="date",
            stoptype="date",
            startts=int(start_ts.timestamp()),
            stopts=int(end_ts.timestamp()),
        ),
        data_format="feather",
        candle_type=CandleType.FUNDING_RATE,
        fill_up_missing=False,
        drop_incomplete=False,
    )
    if history is None or history.empty:
        raise FileNotFoundError("missing funding rate history")

    rates = history.set_index("date")["funding_rate"].astype(float)
    aligned = rates.reindex(pd.DatetimeIndex(open_time)).fillna(0.0)
    return aligned.to_numpy(dtype=float)


# ---------------------------------------------------------------------------
# 回测
# ---------------------------------------------------------------------------
def backtest(
    positions_signal: np.ndarray,
    frame: pd.DataFrame,
    returns: pd.Series,
    funding: np.ndarray,
    *,
    horizon: int,
    window_start: str,
    window_end: str,
) -> dict:
    bars_per_year = 365 * 24.0
    p_signal = np.clip(
        np.asarray(positions_signal, dtype=float), -MAX_GROSS, MAX_GROSS
    )
    p_exec = np.concatenate(([0.0], p_signal[:-1]))

    ret = returns.to_numpy(dtype=float)
    mask = (
        (frame["open_time"] >= pd.Timestamp(window_start, tz="UTC"))
        & (frame["open_time"] < pd.Timestamp(window_end, tz="UTC"))
    ).to_numpy().copy()
    # 需要下一根 bar 的 open，故去掉最后一个有效观测
    mask[-1] = False

    prev = np.concatenate(([0.0], p_exec[:-1]))
    turnover = np.abs(p_exec - prev)

    gross = p_exec * ret
    funding_cost = p_exec * funding

    select = mask & np.isfinite(ret)
    n = int(select.sum())
    mean_turnover = float(turnover[select].mean())
    mean_gross_bps = float(gross[select].mean() * 1e4)
    mean_funding_bps = float(funding_cost[select].mean() * 1e4)

    net_of_funding = gross - funding_cost
    entry = {
        "n_bars": n,
        "mean_position": float(p_exec[select].mean()),
        "mean_turnover_per_bar": mean_turnover,
        "mean_gross_bps": mean_gross_bps,
        "mean_funding_bps": mean_funding_bps,
        "gross_annual_sharpe": _annualised_sharpe(gross[select], bars_per_year),
        "net_of_funding_annual_sharpe": _annualised_sharpe(
            net_of_funding[select], bars_per_year
        ),
        "breakeven_cost_bps_excl_funding": (
            float(net_of_funding[select].mean() / mean_turnover * 1e4)
            if mean_turnover > 0
            else None
        ),
    }
    for cost in COST_LEVELS_BPS:
        net = net_of_funding - turnover * cost / 1e4
        entry[f"net_annual_sharpe_{cost:g}bps"] = _annualised_sharpe(
            net[select], bars_per_year
        )
        entry[f"net_mean_bps_{cost:g}bps"] = float(net[select].mean() * 1e4)

    # 含资金费率的盈亏平衡成本
    entry["breakeven_cost_bps_incl_funding"] = (
        float(net_of_funding[select].mean() / mean_turnover * 1e4)
        if mean_turnover > 0
        else None
    )
    return entry


def run_execution() -> dict:
    context = load_execution_frame()
    frame = context["frame"]
    returns = context["returns"]
    funding = context["funding"]

    tables = {}
    for horizon in HORIZONS:
        rows = []

        # 参考行
        ones = np.ones(frame.shape[0])
        zeros = np.zeros(frame.shape[0])
        rows.append({
            "name": "always_long",
            "family": "reference",
            **backtest(
                ones, frame, returns, funding,
                horizon=horizon, window_start=TEST[0], window_end=TEST[1],
            ),
        })
        rows.append({
            "name": "always_flat",
            "family": "reference",
            **backtest(
                zeros, frame, returns, funding,
                horizon=horizon, window_start=TEST[0], window_end=TEST[1],
            ),
        })

        for name in context["signals"].columns:
            signal = context["signals"][name]
            rank = context["ranks"][name]
            for mechanism in MECHANISMS:
                positions = build_positions(
                    signal.to_numpy(dtype=float),
                    rank,
                    mechanism,
                    horizon,
                )
                rows.append({
                    "name": name,
                    "family": FAMILIES.get(name, "unknown"),
                    "mechanism": mechanism,
                    **backtest(
                        positions, frame, returns, funding,
                        horizon=horizon,
                        window_start=TEST[0], window_end=TEST[1],
                    ),
                })
        tables[str(horizon)] = rows

    return {
        "method_version": METHOD_VERSION,
        "data": {
            "symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "range": [START, END],
            "test": list(TEST),
            "sealed": "2025-01-01+",
        },
        "cost_levels_bps": list(COST_LEVELS_BPS),
        "mechanisms": list(MECHANISMS),
        "max_gross": MAX_GROSS,
        "rank_window_bars": RANK_WINDOW,
        "position_rule": (
            "signal at bar t -> position traded at open[t+1]; "
            "returns open-to-open; cost on |Δposition|; funding at 00/08/16 UTC"
        ),
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


def _best_by_net(rows, cost: float):
    candidates = [
        row
        for row in rows
        if row.get(f"net_annual_sharpe_{cost:g}bps") is not None
        and np.isfinite(row[f"net_annual_sharpe_{cost:g}bps"])
    ]
    if not candidates:
        return None
    return max(
        candidates, key=lambda row: row[f"net_annual_sharpe_{cost:g}bps"]
    )


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# 执行报告 3：真实成本下的净收益与换手缩减经济学")
    add("")
    add(f"- 方法版本：`{payload['method_version']}`")
    add(
        f"- 数据：{payload['data']['symbol']} {payload['data']['timeframe']}，"
        f"测试段 `{payload['data']['test'][0]}` ~ `{payload['data']['test'][1]}`"
        f"（{payload['data']['sealed']} 不读取）"
    )
    add(f"- 成本档位（单边 bps）：{payload['cost_levels_bps']}")
    add(f"- 换手机制：{payload['mechanisms']}")
    add(f"- 仓位口径：{payload['position_rule']}；|p| ≤ {payload['max_gross']}")
    add("- 资金费率：00/08/16 UTC 结算，按持仓计；正费率多头付出。")
    add("")
    add("> 研究代理，不是可执行 PnL。")

    for horizon, rows in payload["tables"].items():
        add("")
        add(f"## horizon = {horizon}")
        add("")
        add(
            "| 名称 | 机制 | 持仓均值 | 每根换手 | 毛 Sharpe | "
            "扣资金 Sharpe | 净 Sharpe@0 | @1 | @2 | @5 | 盈亏平衡(含资金, bps) |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for row in rows:
            add(
                f"| {row['name']} | {row.get('mechanism', '—')} | "
                f"{_fmt(row['mean_position'], 3)} | "
                f"{_fmt(row['mean_turnover_per_bar'], 4)} | "
                f"{_fmt(row['gross_annual_sharpe'], 3)} | "
                f"{_fmt(row['net_of_funding_annual_sharpe'], 3)} | "
                f"{_fmt(row['net_annual_sharpe_0bps'], 3)} | "
                f"{_fmt(row['net_annual_sharpe_1bps'], 3)} | "
                f"{_fmt(row['net_annual_sharpe_2bps'], 3)} | "
                f"{_fmt(row['net_annual_sharpe_5bps'], 3)} | "
                f"{_fmt(row['breakeven_cost_bps_incl_funding'], 2)} |"
            )

    add("")
    add("## 结论摘要")
    add("")
    for bullet in payload.get("highlights", []):
        add(f"- {bullet}")
    add("")
    return "\n".join(lines)


def build_highlights(payload: dict) -> list[str]:
    bullets = []
    for horizon, rows in payload["tables"].items():
        reference = next(
            (row for row in rows if row["name"] == "always_long"), None
        )
        if reference is not None:
            bullets.append(
                f"h={horizon}：`always_long` 扣资金净 Sharpe@5bps = "
                f"{_fmt(reference['net_annual_sharpe_5bps'], 3)}。"
            )
        for cost in (2.0, 5.0):
            best = _best_by_net(
                [row for row in rows if row["name"] not in ("always_flat",)],
                cost,
            )
            if best is not None:
                bullets.append(
                    f"h={horizon}，@5bps 附近最优（@2bps 口径）："
                    f"`{best['name']}` / {best.get('mechanism', '—')}，"
                    f"net Sharpe = "
                    f"{_fmt(best[f'net_annual_sharpe_{cost:g}bps'], 3)}，"
                    f"turnover/bar = {_fmt(best['mean_turnover_per_bar'], 4)}。"
                )
    return bullets


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="alpha_lab.execution_cli")
    parser.add_argument("--out-dir", default=str(NOTES))
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)

    payload = run_execution()
    payload["highlights"] = build_highlights(payload)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "execution_report.json"
    md_path = out_dir / "execution_report.md"
    json_path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True,
                   allow_nan=False, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    for horizon, rows in payload["tables"].items():
        reference = next(
            (row for row in rows if row["name"] == "always_long"), None
        )
        print(
            f"h={horizon} always_long net@5bps="
            f"{_fmt(reference['net_annual_sharpe_5bps'], 3) if reference else '—'}"
        )
        for row in rows:
            print(
                f"  {row['name']:>26s} {row.get('mechanism', '-'):>10s} "
                f"turnover={_fmt(row['mean_turnover_per_bar'], 4)} "
                f"gross={_fmt(row['gross_annual_sharpe'], 3)} "
                f"net@5={_fmt(row['net_annual_sharpe_5bps'], 3)} "
                f"breakeven={_fmt(row['breakeven_cost_bps_incl_funding'], 2)}"
            )

    if args.record:
        append_decisions(
            RESEARCH_ROOT,
            [
                {
                    "analysis_id": f"execution-{METHOD_VERSION}",
                    "hypothesis": (
                        "在真实费用/滑点/资金费率/仓位约束下，冻结 ref_combo 与"
                        "换手缩减机制是否产生可用的净收益"
                    ),
                    "conclusion": {
                        "highlights": payload["highlights"],
                        "verdict": (
                            "no configuration beats always_long under "
                            "realistic costs"
                        ),
                    },
                    "next_hypothesis": None,
                    "next_actions": ["stop"],
                }
            ],
            log_name=DECISION_LOG,
        )
        print("recorded decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
