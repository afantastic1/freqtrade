# Plan: BTC+ETH multi-timeframe (5m/15m/1h/4h) alpha research

## Environment
- Real research repo: `/home/von/workspace/code/coin/freqtrade`
- Skill: `.opencode/skills/freqtrade-alpha-research` (CLI writes to `user_data/research/alpha.sqlite`)
- Data: `user_data/data/binance` (+futures) for BTC & ETH, 5m/15m/1h/4h, 2022-01-01+
- Reuse: `user_data/research/lib` (data, context, shortcycle, alpha_eval, tradability, market_rules, risk_model)

## Data policy
- EXPLORATORY dev: 2022-01-01 .. 2025-01-01 (4 years)
- 2025 = REFERENCE_CONTAMINATED (not used as independent OOS here)
- 2026 = LOCKED, never read (user says OOS)
- No live trading; single run at a time (no parallelism)

## Scope (user request)
- Assets: BTCUSDT, ETHUSDT (perpetual + spot for basis)
- Timeframes: 5m, 15m, 1h, 4h
- Multiple economic theories -> factors -> evaluate -> refine from results
- 300 USDT tradability gate

## Economic theories to pre-register (multiple)
1. Crowding / funding cost (funding, funding_z, crowd_score)
2. Spot-perp basis convergence (basis, basis_z, basis_chg, spot_fut_div)
3. Short-horizon momentum vs reversal (ret_1/4/16, ema_cross, mom_z)
4. Volatility / channel mean-reversion (range_pos, bb_pos, vol_ratio, atr_pct)
5. Volume / liquidity shock (vol_z, vwap_dev, amihud, vol_price_corr)
6. Spot-lead vs perp-lead (vol_lead, ret_lead) - microstructure

## Method
- Build per-asset panel via extended data.merged(asset, tf); features from shortcycle.features
- Executable labels: t close decision -> t+1 open entry -> hold h bars -> close exit
- Stats: IC, RankIC, ICIR, NW-t, quantiles, monotonicity, turnover, orthogonal增量, per-year
- BH-FDR across hypothesis family; 300U tradability gate per (factor x main horizon)
- Persist via controlled `alpha.py import` bundle (append-only)

## Sequence
inspect -> hypothesize -> implement -> check (no-lookahead) -> evaluate -> critique -> persist -> refine

## Notes
- Prior P6/P7/P8 already covered BTC 15m/1h extensively; ETH and 5m/4h are new.
- Must not silently revise prior conclusions.
