---
name: freqtrade-research
description: Autonomously research Binance BTC USDT perpetual trading hypotheses using Freqtrade, historical futures and spot OHLCV, funding/mark data, and short-window OI. Run reproducible backtests, diagnose failures, preserve experiment memory, and enforce out-of-sample validation with no live trading.
compatibility: opencode
---

# Freqtrade autonomous research (local Python / uv)

## Mandate
Act as a skeptical quant researcher, not a parameter-mining bot. Your job is to propose falsifiable economic hypotheses, implement Freqtrade IStrategy candidates, evaluate them with real historical data, explain failures, and diversify research directions. A backtest win is not evidence of future profit. Never execute a live order.

## Available data and limits
- Exchange: Binance, symbol `BTC/USDT:USDT` (linear isolated perpetual).
- Futures OHLCV: 5m, 15m, 1h, 4h from 2022-01-01 through approximately 2026-10-15.
- Funding rate and mark: historical data from 2022 onward; verify alignment and gaps.
- Spot BTC/USDT OHLCV: user reports locally available; inspect actual coverage before use.
- Open interest: about 20 days only. Treat as a separate recent exploratory dataset. **Never** fill missing years with zeros, carry-forward, interpolated, or invented OI.
- CVD/aggTrades/order book data are explicitly **out of scope**. Do not download or use them.

## Setup and absolute safety
1. Inspect Freqtrade 2026.9 local source, available strategy builder skill, CLI help, and existing data without changing the application's operational setup.
2. Work in `user_data/research/` with independent `strategies/`, `experiments/`, `reports/`, and `registry.jsonl`.
3. Create dedicated backtest config with no exchange API credentials, Telegram token, or API server. Use `dry_run: true`, `initial_state: stopped`, `trading_mode: futures`, `margin_mode: isolated`, isolated paths, only BTC pair, and an appropriate static pairlist. Keep any required public-network proxy configuration free of secrets.
4. Never load, print, alter, or copy real account secrets. Never modify the user's `user_data/config.json`, live config/database, or running bot. Never execute `freqtrade trade`, `freqtrade-client start`, exchange order endpoints, or any script that can trade or transfer funds.
5. Before using third-party skill scripts, inspect them for Docker setup, config overwrite, credentials access, networking, and trading commands. Adapt research commands to the existing `.venv`; do not reinstall the project.
6. Start with a data audit: actual coverage, gaps, duplicated bars, timezone, candle close and data availability time, inconsistencies between futures/spot/mark/funding. Stop or narrow test dates when required data are absent.

## Research tracks
**Track A – Long history, main research:** Futures OHLCV + spot OHLCV + funding and mark (only where valid). Study trend continuation, volatility expansion, mean reversion, and spot-futures basis/relative strength. Every hypothesis must state *why* it might predict the next return, where it should fail, what would falsify it, and the exact features/signals used.

**Track B – Recent OI exploratory:** 20-day OI with same-period candles. Inspect lagged OI changes, price/OI states, and conditional distributions; report small-sample uncertainty. No claim of robust historical alpha or live readiness from Track B; no fitting complex models on this short window. OI inclusion must not shrink Track A evaluation invisibly.

## Experimental discipline
- Freeze a chronological split *before* testing: initial proposal development 2022-01 through 2024-12, reusable validation 2025-01 through 2025-12, locked final test 2026-01 through 2026-09. Adjust only if verified data availability requires it; record change and reason.
- Never inspect, tune against, or repeatedly run the locked final test during exploration. Run it once for nominated finalist(s) after candidate selection and freeze.
- Compare candidates against a no-trade baseline, long-only exposure, and the existing BTCTrendBreakout V1 where reproducible. Preserve fees, funding assumptions, leverage, initial equity, and exposure limits consistently. Add conservative slippage sensitivity tests; do not claim precision unsupported by the backtester.
- Use completed-candle features only. For spot/futures or higher-timeframe joins enforce `available_at <= signal_decision_time`. Do not use future funding values or the closing value of an unfinished informative candle.
- Evaluate number of trades, wins/losses, long/short separately, average trade net return, profit factor, Sharpe (with caveats), maximum drawdown, drawdown duration, expectancy, fee/turnover burden, adverse regimes, and behavior under tighter costs.
- Run `freqtrade lookahead-analysis` and `freqtrade recursive-analysis` on viable candidates; these tools cannot guarantee absence of bias. Inspect strategy code for shift/rolling resampling leakage and confirm trade fills realism.
- Risk controls for experiments: isolated positions, max_open_trades=1, at most 1-2x leverage, position sizing risk budget <=0.5% account per trade subject to exchange min-notional/lot constraints; no signal may override those limits. Distinguish leveraged P&L vs price % stop distance.
- Reject empty/inadequate sample tests, unrealistic fills, API/data errors, and any candidate failing checks. Never fabricate missing results.

## Autonomous iteration
For each iteration:
1. Read `registry.jsonl` and prior reports. Identify whether a new idea is genuinely distinct from failed hypotheses and avoid starving a class of signals.
2. Write a short hypothesis card: mechanism, features, decision timing, long/short rules, expected regime, failure mode, and evaluation plan.
3. Implement a standalone IStrategy in the research strategies folder, run lint/import checks, then backtest on development period.
4. Parse machine-readable Freqtrade results, create a one-page diagnostic report including exit reasons, drawdown and long/short breakdown; do not rely on terminal summaries alone.
5. If useful, run validation and investigate performance degradation. Record failures as informative outcomes.
6. Append an immutable registry record with unique experiment ID, code hash, config hash, exact dataset ranges, parameters, command, metrics, artifacts, status and next hypothesis.
7. Decide refine / pivot / reject / candidate, giving a rationale. Each round must seek *better explanation or robustness*, not merely higher in-sample profit.

First autonomous batch: 3 unrelated hypotheses (trend continuation, mean reversion, volatility expansion), maximum 2 revisions per hypothesis, max 9 experiments overall. Cap expensive optimization/search, and stop on data errors, cost overruns, repeated identical results, or no credible improvements. Never automatically run a live bot. Ask for human approval before a separate Dry-run deployment.

## Output contract
- `user_data/research/reports/data_audit.md`
- `user_data/research/registry.jsonl`
- versioned research strategy files and config hashes
- per-experiment hypotheses and diagnostics in `experiments/<id>/`
- `user_data/research/reports/summary.md` comparing hypotheses, invalid trials, validation results, uncertainty, and whether any candidate merits dry-run.

When first invoked, **only** audit data, inspect existing skill and Freqtrade CLI, create the safe isolated research environment, and print proposed commands. Do not start the 9-experiment batch until explicit follow-up approval. After approval, operate autonomously within the fixed limits and report results.
