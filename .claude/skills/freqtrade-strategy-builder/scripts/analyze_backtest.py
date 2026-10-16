#!/usr/bin/env python3
"""Create a concise Markdown summary from a Freqtrade backtest JSON export."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path, help="Backtest JSON file")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown to this file")
    return parser.parse_args()


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def find_strategy(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    strategies = payload.get("strategy")
    if isinstance(strategies, dict) and strategies:
        name, result = next(iter(strategies.items()))
        if isinstance(result, dict):
            return str(name), result
    return str(payload.get("strategy_name", "unknown")), payload


def find_trades(result: dict[str, Any]) -> list[dict[str, Any]]:
    trades = result.get("trades", [])
    if isinstance(trades, list):
        return [trade for trade in trades if isinstance(trade, dict)]
    return []


def pick(result: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in result and result[name] is not None:
            return result[name]
    return None


def fmt(value: Any, kind: str = "number") -> str:
    numeric = number(value)
    if numeric is None:
        return "n.a."
    if kind == "percent":
        return f"{numeric * 100:.2f}%"
    if kind == "money":
        return f"{numeric:,.2f}"
    return f"{numeric:.3f}"


def build_report(payload: dict[str, Any], source: Path) -> str:
    strategy_name, result = find_strategy(payload)
    trades = find_trades(result)
    profits = [
        value
        for trade in trades
        if (value := number(trade.get("profit_ratio"))) is not None
    ]
    wins = sum(value > 0 for value in profits)
    losses = sum(value < 0 for value in profits)
    win_rate = wins / len(profits) if profits else None
    median_trade = statistics.median(profits) if profits else None

    lines = [
        "# Freqtrade backtest summary",
        "",
        f"- **Source file:** `{source}`",
        f"- **Strategy:** `{strategy_name}`",
        f"- **Trades:** {len(trades) or pick(result, ['total_trades', 'trade_count']) or 0}",
        f"- **Win rate:** {fmt(win_rate, 'percent')}",
        f"- **Median trade:** {fmt(median_trade, 'percent')}",
        f"- **Total profit:** {fmt(pick(result, ['profit_total', 'total_profit']), 'percent')}",
        f"- **Absolute profit:** {fmt(pick(result, ['profit_total_abs', 'total_profit_abs']), 'money')}",
        f"- **Maximum drawdown:** {fmt(pick(result, ['max_drawdown_account', 'max_drawdown']), 'percent')}",
        f"- **Sharpe:** {fmt(pick(result, ['sharpe']))}",
        f"- **Sortino:** {fmt(pick(result, ['sortino']))}",
        f"- **Profit factor:** {fmt(pick(result, ['profit_factor']))}",
        f"- **Winning / losing trades:** {wins} / {losses}",
        "",
        "## Review gates",
        "",
        "- Confirm fees, slippage, timerange, pairs, and timeframe.",
        "- Run lookahead-analysis and recursive-analysis separately.",
        "- Compare against an untouched out-of-sample period.",
        "- Do not infer live profitability from this report alone.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ERROR: file not found: {args.result}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("ERROR: top-level JSON value must be an object", file=sys.stderr)
        return 2

    report = build_report(payload, args.result)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

