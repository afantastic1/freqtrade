# Factor catalog

Classify factors as:

- **Baseline:** sourced from a public library such as Qlib Alpha158/Alpha101. Record source version, license, and whether the expression depends on cross-sectional operations.
- **Adapted:** modified for crypto spot/perpetual, funding, volatility, or trend data. Record the original expression and why it changed.
- **Novel:** proposed from an explicit economic mechanism and falsifiable prediction.

Record at least: factor and hypothesis IDs, name, canonical formula/AST and hash, semantic family, inputs, lookback, frequency, availability rule, expected direction, source/license, implementation hash, creation time, and parameter/instance hash where applicable. For short-cycle factors record the K-line `timeframe` as well; keep windows specified in bars explicit about the timeframe they refer to.

Use an auditable operator allowlist, for example returns, rolling mean/std, EWM, z-score, time-series rank, ATR, volatility, cross-asset spread, safe division, lag, and interaction. Do not evaluate arbitrary formula strings or use `eval`. Keep formula implementation deterministic and covered by project tests.

Single-asset BTC does not support stock-universe cross-sectional rank/neutralization as originally defined. Mark such operators unsupported; do not silently replace them with time-series ranking while claiming the source factor is unchanged. Import only expressions and required operators, not an entire dependency stack. Preserve license provenance and run deterministic fixture comparisons before cataloging an external expression.
