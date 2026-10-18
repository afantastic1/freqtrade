# Research protocol

## Time and labels

Normalize timestamps to UTC and distinguish `event_ts`, `available_ts`, `bar_open_ts`, and `bar_close_ts`. At decision time `t`, use only inputs whose `available_ts <= t`. A full candle's OHLCV is available only after its close; do not assume an order can fill at that already observed close.

Distinguish announced/predicted funding from settled funding. Never backfill settled values before settlement. Join higher timeframes (for example 4h into 1h) as of the completed higher-timeframe candle's availability, leaving earlier rows unavailable. Validate paired spot/perpetual event times, lag, missingness, and volume units. Do not use locked or too-short open-interest data.

Define executable labels from the earliest plausible entry after signal availability (such as next-bar open or a conservative fill model). A close-to-close target may be reported separately as theoretical, never silently as executable return. Record horizon, direction, overlap, fees, and execution assumptions. Keep each horizon and its test family separate; for longer horizons use 6/12/24/48h outcomes, and for short-cycle work (5m–4h) keep the `15m/30m/1h/2h/4h` duration windows separate from the K-line timeframe that produces them.

## Short-cycle timeframes and labels

Treat the K-line timeframe (how often you may act) and the prediction window (how long you hold) as independent. Convert a duration window to bars per timeframe and require an integer multiple; refuse (do not round) a window such as 1h on a 4h timeframe or 15m on an 1h timeframe. Prefer the more conservative availability rule for short bars: decision at bar close, higher-timeframe/context inputs available only after their own close plus publication lag. Do not use open interest where only a very short history exists, and do not use locked data.

## Causal and deterministic checks

Before evaluation, check input access, schema, missingness, availability alignment, lookahead, valid parameters, and deterministic output. Prefer controlled loader and evaluator code. For multi-timeframe, funding, or any revised data, explicitly test the availability rule. If checks cannot be run, label the candidate unchecked and do not backtest it.

## Evaluation and interpretation

For a single-asset series, use time-series predictive measures rather than importing cross-sectional stock IC semantics. Depending on available sample size and tools, report IC/rank IC, rolling stability, ICIR, quantile outcome and monotonicity, regime/year splits, effective sample blocks, confidence intervals, and HAC or block-bootstrap uncertainty. Record test-family size and an appropriate multiple-testing adjustment such as BH/FDR when supported.

Compare with relevant baselines using correlation, partial/conditional regression, and out-of-sample incremental value where data supports it. Report coverage, parameter sensitivity, turnover, economic magnitude, uncertainty, cost sensitivity, and falsification condition. An IC is predictive association, not proof of profit. Judge independent alpha and absolute tradability separately.

Use `REJECTED` for invalid data/causality or a properly tested failure, `INSUFFICIENT` for inadequate evidence, and `PROMISING` for plausible exploratory evidence that still needs independent validation. `VALIDATED` requires frozen independent OOS evidence under an approved protocol. Thresholds must be explicit and versioned; do not rely on universal magic IC or t-stat cutoffs.
