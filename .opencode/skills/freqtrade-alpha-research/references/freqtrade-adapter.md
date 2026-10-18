# Freqtrade adapter boundary

Before strategy work, inspect the repository's installed Freqtrade version and the actual strategy/risk interfaces. Do not assume current upstream APIs match this checkout. Reuse the audited `RiskCfg` and fail-closed risk behavior; do not weaken existing limits to satisfy minimum order sizes. The design's initial risk suggestions are not guarantees and are not a substitute for the repository's audited source of truth.

Only a `PROMISING` or better factor with an explicit executable entry/exit rule may be translated to a strategy. Treat signal threshold, holding period, filters, stop, and stake as new versioned experiments. Confirm futures mode, margin/leverage, long/short behavior, exchange constraints, precision, historical minimum size/notional, fees, and funding assumptions before backtesting.

## Small-account short-cycle execution

For a small (about 300 USDT) account trading short horizons, executability is frequently the binding constraint, not signal quality. Before any backtest, check the same limits the live path enforces, via `RiskCfg` and `market_rules`:

- **Minimum order economics.** Minimum notional / minimum amount (BTC step) imply a minimum viable capital; as price rises the `amount_min × price` term can exceed the notional floor and make a small account infeasible at some prices. This is a real constraint, not a modelling artifact.
- **Costs and funding over the exact holding duration.** Round-trip fees plus slippage are charged per trade; funding accrues over the held hours. Higher cadence multiplies fees and slippage.
- **Stop distance, leverage and per-trade risk.** Stop distance must not be widened to reach a minimum size. Keep per-trade risk within `max_model_risk` (2%); the account may be too small to trade at all under these rules, and that is a valid, reportable result.
- **Known limitations.** The frozen risk model assumes no slippage in sizing, does not include gap/tail losses or funding inside the sizing decision, and its offline minimum-notional figure is not re-verified against the live exchange. Report these as residual risk; do not present the gate as a guarantee.

A `PASS` from the tradability gate means the execution constraints were satisfied for the evaluated price/stop/edge; it is not a profit forecast and does not upgrade the evidence level.

Keep sizing cost buffers, research-label cost assumptions, and actual backtest fees/funding/slippage separate. Test base and stress costs. Do not describe sizing buffers as fees deducted from PnL. Report net return, CAGR, drawdown definitions, per-trade risk, and any stop/cooldown behavior with consistent, explicit meanings.

Run causal checks before backtesting. Use the installed version's lookahead analysis and, where applicable, recursive analysis as supporting checks, not proof by themselves. If strategy behavior, risk controls, or cost data cannot be verified, stop before the backtest and report the missing evidence. This workflow never enables live trading or order placement.
