# Robust strategy research workflow

## Define the hypothesis

Write the market hypothesis before writing indicators. State why the signal might
exist, when it should fail, the expected holding period, and which costs could
eliminate it.

## Prepare the data

- Use the correct exchange, market type, pairs, and timeframe.
- Record the exact timerange and data-download command.
- Inspect gaps, delistings, newly listed pairs, and survivorship effects.
- Include enough warm-up data for all indicators.
- Avoid using today's pair list to represent a historical dynamic universe without
  acknowledging survivorship bias.

## Build a baseline

- Start with simple rules and fixed parameters.
- Include realistic fees.
- Model slippage or repeat tests under worse execution assumptions.
- Keep order types, pricing, and position sizing aligned with intended deployment.
- Compare against buy-and-hold or another simple benchmark where meaningful.

## Eliminate invalid evidence

Run lookahead analysis. Typical sources of leakage include:

- `shift(-1)` or other future rows
- centered rolling windows
- using global min, max, mean, or normalization over the complete dataframe
- merging higher-timeframe or informative data incorrectly
- labels or model features computed using future outcomes

Run recursive analysis. Indicators with long memory can vary materially when the
startup candle count changes.

## Split periods

At minimum, use:

1. In-sample period for development
2. Validation period for selecting among alternatives
3. Untouched out-of-sample period for final evaluation

For unstable markets, prefer walk-forward evaluation across multiple regimes.
Never repeatedly tune against the final out-of-sample period.

## Optimize carefully

- Optimize a small, economically defensible parameter space.
- Keep epoch count and random seed in the research log.
- Do not optimize every available space merely because the tool allows it.
- Prefer stable plateaus over one narrow best parameter combination.
- Penalize excessive drawdown, complexity, and turnover.
- Re-run the selected parameters without cached results.

## Stress tests

Repeat the evaluation with:

- Higher fees
- Adverse slippage
- Entry delayed by one candle where appropriate
- Fewer and more pairs
- Different bull, bear, and sideways periods
- Reduced liquidity assumptions
- Missing trades or intermittent exchange connectivity

## Review the result

Minimum report:

- Strategy version or commit
- Freqtrade version
- Config fingerprint, with secrets excluded
- Exchange, mode, pairs, timeframe, timerange
- Fee and slippage assumptions
- Trade count and exposure
- Return, drawdown, Sharpe, Sortino, profit factor
- Average and median trade
- Win rate and longest losing streak
- Pair-level concentration
- In-sample and out-of-sample comparison
- Lookahead and recursive-analysis results

## Promotion gates

Research to dry-run:

- No lookahead bias
- Acceptable recursive variance
- Positive and plausible out-of-sample evidence
- Drawdown within stated limit
- Results robust to modestly worse costs
- Config passes static checks

Dry-run to live:

- Stable operation over a user-defined observation period
- Order behavior matches backtest assumptions
- Exchange permissions are trade-only, with withdrawals disabled
- Monitoring, alerts, logs, stop procedure, and incident plan are ready
- Stake and loss limits are explicitly approved

