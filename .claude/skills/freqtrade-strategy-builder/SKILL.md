---
name: freqtrade-strategy-builder
description: "Build, review, backtest, optimize, and safely operate Freqtrade algorithmic trading strategies. Use when the user asks about Freqtrade, crypto trading bots, strategy generation, historical-data downloads, backtesting, hyperopt, lookahead-bias checks, recursive analysis, dry-run deployment, or analysis of Freqtrade results. Supports Claude Code and Codex in Docker-first Linux, macOS, and WSL environments."
license: MIT
compatibility: "Requires Docker with Compose v2 for the recommended workflow, or an existing Freqtrade installation. Python 3.10+ is needed only for the bundled local validation and reporting scripts."
metadata:
  author: Diem-Nsakala
  version: "1.0.0"
  project: freqtrade
---

# Freqtrade Strategy Builder

## Purpose

Use this skill to turn a trading idea into a reproducible Freqtrade workflow:

1. Translate the idea into explicit, testable rules.
2. Generate or modify a strategy.
3. Validate configuration and code.
4. Download the required historical data.
5. Backtest with realistic assumptions.
6. Check lookahead and recursive bias.
7. Optimize only after the baseline passes.
8. Compare out-of-sample results.
9. Deploy in dry-run mode.
10. Enter live mode only after explicit user approval and independent safeguards.

This skill assists with software and research. It does not promise profitability or replace financial, legal, tax, or security advice.

## Mandatory Safety Rules

1. Default to `dry_run: true`. Never silently change it to `false`.
2. Never start live trading without explicit user confirmation in the current conversation.
3. Never request, display, commit, log, or store exchange secrets in strategy files, example configs, shell history, or reports.
4. Use environment variables, an ignored private config, or a secret manager for credentials.
5. Never enable withdrawal permissions on an exchange API key.
6. Recommend IP allowlisting and a dedicated sub-account when the exchange supports them.
7. Before live trading, require successful backtest, lookahead analysis, recursive analysis, out-of-sample test, and dry-run observation.
8. Treat backtest and hyperopt results as estimates, not guarantees.
9. Do not invent command output. Run commands when tools are available; otherwise clearly label proposed commands.
10. Stop if configuration validation fails, data is missing, or the strategy produces obviously invalid values.

Read `references/safety-and-risk.md` before any deployment or live-trading request.

## Environment Discovery

Before editing or running anything:

1. Locate the project root, `docker-compose.yml`, `user_data/config*.json`, and `user_data/strategies/`.
2. Run `scripts/ft.sh doctor`.
3. Inspect the installed Freqtrade version and strategy interface.
4. Check Git status before modifying an existing repository.
5. Preserve unrelated user changes.
6. Prefer the project's existing conventions over bundled examples.

If there is no project, use:

```bash
./scripts/bootstrap.sh ./ft_userdata
```

This downloads the official stable Docker Compose file, creates `user_data`, and installs safe starter assets. It does not start a bot.

## Clarify the Strategy

For non-trivial requests, establish these inputs before implementation:

- Market and exchange
- Spot or futures
- Long-only or long/short
- Pair universe
- Timeframe
- Entry conditions
- Exit conditions
- Stop-loss and optional trailing stop
- Position sizing and maximum concurrent trades
- Backtest period and out-of-sample period
- Primary metric and acceptable drawdown

If the user has not specified them, propose conservative research defaults:

- Spot, long-only
- BTC/USDT and ETH/USDT
- 1h timeframe
- Maximum 2 concurrent trades
- Dry-run wallet only
- At least one in-sample and one later out-of-sample period

Do not present these defaults as personalized investment recommendations.

## Strategy Implementation

Start from `assets/SafeEmaRsiStrategy.py` or the project's current strategy.

Requirements:

- Use `INTERFACE_VERSION = 3`.
- Keep indicator calculation in `populate_indicators`.
- Use vectorized pandas conditions in entry and exit methods.
- Include a positive-volume guard in entry and exit signals.
- Set `startup_candle_count` high enough for the longest indicator.
- Avoid future data, negative shifts, centered rolling windows, and full-dataframe aggregations that leak future information.
- Keep protections and risk controls explicit.
- Add concise comments explaining the trading hypothesis, not every line of syntax.
- Do not add exchange keys or account-specific data to Python files.
- Run Python compilation or the repository's tests after modification.

For futures, do not merely set `can_short = True`. Also validate exchange support, trading mode, margin mode, leverage handling, liquidation risk, stop-loss behavior, and funding-cost assumptions.

## Required Research Workflow

Follow this order and do not skip directly to hyperopt:

### Baseline

1. Validate the config:
   ```bash
   python3 scripts/validate_config.py user_data/config.json
   ```
2. Download data:
   ```bash
   ./scripts/ft.sh download-data --config user_data/config.json \
     --pairs BTC/USDT ETH/USDT --timeframes 1h --days 730
   ```
3. Run the baseline:
   ```bash
   ./scripts/ft.sh backtest --config user_data/config.json \
     --strategy SafeEmaRsiStrategy --timerange 20240101-20251231 \
     --export trades
   ```
4. Export and summarize results:
   ```bash
   python3 scripts/analyze_backtest.py user_data/backtest_results/latest.json
   ```

### Bias checks

Run both:

```bash
./scripts/ft.sh lookahead-analysis --config user_data/config.json \
  --strategy SafeEmaRsiStrategy --timerange 20240101-20251231
```

```bash
./scripts/ft.sh recursive-analysis --config user_data/config.json \
  --strategy SafeEmaRsiStrategy --timerange 20240101-20251231 \
  -p 20 50 100 200
```

Any confirmed lookahead bias blocks optimization and deployment. Material recursive variance requires increasing startup candles or revising indicators.

### Hyperopt

Optimize only after the baseline and bias checks pass:

```bash
./scripts/ft.sh hyperopt --config user_data/config.json \
  --strategy SafeEmaRsiStrategy \
  --hyperopt-loss SharpeHyperOptLossDaily \
  --spaces buy sell roi stoploss trailing \
  --epochs 200 --timerange 20240101-20241231
```

Never select parameters using the same period used for final evaluation. Re-run the chosen parameters on a later untouched out-of-sample period and compare them against the baseline and a simple benchmark.

## Evaluation Standards

Report at least:

- Backtest dates, exchange, pairs, timeframe, and fees
- Number of trades and trade frequency
- Total and annualized return when available
- Maximum drawdown
- Sharpe, Sortino, and profit factor when available
- Win rate and expectancy
- Best and worst pair
- Longest losing streak
- Exposure and maximum concurrent trades
- Bias-analysis status
- In-sample versus out-of-sample comparison

Reject or flag results when:

- There are too few trades for meaningful inference.
- Performance depends on one pair or a few outliers.
- Drawdown exceeds the user's stated limit.
- Small fee or slippage changes destroy the result.
- Out-of-sample performance collapses.
- Hyperopt produces implausibly complex or unstable rules.

Read `references/research-workflow.md` for the detailed review checklist.

## Dry-Run Deployment

Before starting:

1. Confirm `dry_run` is `true` with `scripts/validate_config.py`.
2. Confirm API keys are absent from tracked files.
3. Confirm strategy name, exchange, pairs, stake currency, and maximum open trades.
4. Start in the foreground first so errors remain visible:

```bash
./scripts/ft.sh dry-run --config user_data/config.json \
  --strategy SafeEmaRsiStrategy
```

Do not start a background service unless the user asks for it. Explain how to stop it and where logs are written.

## Live-Trading Gate

Live mode is intentionally double-gated:

1. Obtain explicit user approval after presenting the exact exchange, config, strategy, stake, and risk limits.
2. The user or authorized operator must set:

```bash
export FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES
```

3. Run:

```bash
./scripts/ft.sh live --confirm-live --config /path/to/private-live-config.json \
  --strategy StrategyName
```

Never weaken or remove this gate. Never place real orders merely because a user asked for a strategy, backtest, installation, or dry-run.

## Output Format

When completing work, report:

1. Files created or changed
2. Commands executed
3. Validation and test results
4. Backtest period and data assumptions
5. Key performance and risk metrics
6. Bias-check status
7. Whether the system remains in research, backtest, dry-run, or live mode
8. Concrete next step

Use clear labels for observed results versus assumptions. Do not claim that a strategy is profitable without qualifying the period, costs, and limitations.

## Bundled Resources

- `scripts/bootstrap.sh`: initialize a Docker-first project
- `scripts/ft.sh`: guarded Freqtrade command wrapper
- `scripts/validate_config.py`: static safety and configuration checks
- `scripts/analyze_backtest.py`: summarize exported JSON results
- `scripts/smoke_test.sh`: local tests for bundled resources
- `assets/SafeEmaRsiStrategy.py`: conservative educational template
- `assets/config.dry-run.example.json`: secret-free dry-run config
- `references/commands.md`: command reference and official documentation
- `references/research-workflow.md`: robust backtest and review process
- `references/safety-and-risk.md`: security and live-trading gates

