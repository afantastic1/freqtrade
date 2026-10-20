# Alpha Lab v1.1 cross-sectional research protocol (draft)

Status: **development, not frozen**. The v1.0 `time_series` interface, old SQLite rows, and historical statistical method versions retain their original meaning. A cross-sectional result is stored in `cs_evaluations`, never in the legacy `evaluations` metric columns.

## Scope and data

- Binance USDⓈ-M Futures OHLCV, UTC, 5m/15m/1h/4h. Research dates are `[2022-01-01, 2025-01-01)`; 2022–2024 is a repeatedly used development sample. The 2025+ reserve is excluded.
- `MarketPanel` has a unique `(open_time, symbol)` index and retains only observed candles. Missing bars and pre-listing periods are absent. `available_time` is the completed candle's close time plus an explicitly assumed delay. Features at a section use only values with availability at or before that section's decision time.
- Freqtrade Futures Feather is the preferred source. The Maintainer converts a trusted bounded source to a CSV panel with `panel_cli`. Its quality sidecar reports first and last bars, count, internal gaps, coverage, and data fingerprint. Freqtrade's Feather loader may read the whole source before filtering, so **do not run preparation on a source containing 2025+ rows during held-out-safe acceptance**.
- Fixed Universe membership is the intersection of declared symbols and observed candles. It is subject to survivorship bias and cannot establish historical tradeability. Dynamic membership uses prior available quote turnover, observed listing age, and trailing coverage. Its rules and hash are part of identity. Missing bars never become members.

## Feature and label contract

- Legacy market-only config remains accepted. Versioned configs use `feature_schema_version: alpha-cs-feature-schema-v1` and fixed engine-owned market transforms or a fundamental declaration `{kind: fundamental, metric, max_age, snapshot_pins:[{dataset_id, sha256}]}`. The Runner reads fundamentals only through `FundamentalDataAdapter` with `strict_pit`; exploratory frames and candidate Python are never accepted. The snapshot digest must be the actual Catalog digest, and PIT sidecars must pass row-level evidence validation. The current 14 development snapshots have no qualifying attestations and are all rejected by this interface.
- Per-asset rolling calculations group by symbol and require consecutive bars. `cs_rank`, `cs_zscore`, and `cs_demean` compare assets at a single UTC open time; fixed relative features demean the same-time section. Fundamental joins use the versioned strict-precedence policy and do not match equal timestamps. Missing values stay null; complete-case evaluation and preflight feature coverage report row loss and effective assets. Arbitrary cross-asset Python is not accepted.
- At completed bar *t*, research return is `close[t+h] / open[t+1] - 1` for each asset, provided every intervening bar exists. The label becomes available with bar *t+h*. Relative return subtracts the mean raw return of eligible assets at *t* with valid labels; at least two labels are required. Relative returns are formed only for retrospective targets, never features.
- `entry_time_feasible` explicitly tests whether the reference next open is after the feature decision time. With a positive publication delay it can be false. These targets are **research proxy labels**, not executable fills or trading PnL.

## Split, model, and evaluator

- All symbols use the same UTC `train_end` and `test_end`. Training rows require `open_time < train_end` and `label_available_time <= train_end`; testing rows use `[train_end + embargo, test_end)`. This label-availability rule purges overlap. Embargo defaults to zero because the current one-way chronological split has no subsequent training fold; a future multiple-fold protocol must reassess it.
- Ranking baselines are the first declared raw factor and fixed relative Momentum/Volatility (4 bars). The multifeature model is deterministic Ridge on relative return. Means, scales, and weights fit only training rows. `time_series` retains its Logistic model.
- At each UTC section with at least five valid nonconstant assets, compute Pearson CS-IC and Spearman CS-RankIC. The reported means average **sections**, not asset rows. Fewer than eight valid sections is `INCONCLUSIVE`; no valid section is `NOT_EVALUABLE`. Constant factors, missing labels, and degenerate ranks do not produce fabricated zero scores.
- The method `cs-time-block-centered-v1` samples contiguous UTC sections with a block length specified in the config, reports percentile 95% intervals and a two-sided p value from the centered bootstrap null. HAC uses Bartlett weights with lag `block_size - 1`; both rely on approximately stationary section-level dependence. The power and MDE estimate use bootstrap SE, normal approximation, two-sided alpha 0.05, power 0.8. An assumed CS-RankIC effect of 0.02 is displayed for sensitivity, **not an acceptance threshold**.
- Monthly and annual mean CS-RankIC signs, Top-Bottom quantile spread, rank turnover, and one-sided cost in bps are descriptive. Spread is a proxy derived from reference labels; it is not executable PnL.
- Holm and BH are reported across the four model/baseline comparisons within a run. All distinct research keys evaluated on the same 2022–2024 development set belong to a fixed wider family. The read-only `family_review` gateway request recalculates Holm/BH across stored evaluated comparisons, deduplicating identical config hashes after artifact recovery. Therefore this draft produces `EVALUATED`, `INCONCLUSIVE`, or `NOT_EVALUABLE` only and never `SUPPORTED` or `REJECTED`.

## Identity, persistence, and decisions

The cross-sectional research key hashes engine/protocol/method versions, mode, Universe ID and hash, declared symbols, timeframe, horizon, content fingerprint of the bounded panel, fixed transform source hash, evaluation code hash, and full config including split and statistics. Completed runs are reusable only when summary, predictions, and every manifest artifact hash agree. Failed executions remain failed. `cs_evaluations` is append-only: identical evidence is idempotent, conflicting evidence raises. The old `evaluations` table is untouched. The public Research Decision CLI appends interpretation separately to `cs_research_decisions`, bound to the completed run and transform source hash.

## Commands

Example config: [`cross-sectional-example.json`](cross-sectional-example.json). For a source already verified to contain no 2025+ rows:

```bash
.venv/bin/python -m alpha_lab.panel_cli --symbol BTC/USDT:USDT --symbol ETH/USDT:USDT --timeframe 1h --datadir user_data/data/binance --start 2022-01-01 --end 2025-01-01 --output user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv
.venv/bin/python -m alpha_lab.cross_sectional_cli --config user_data/research/alpha_lab/candidates/example/config.json --panel-csv user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv
.venv/bin/python -m alpha_lab.research_cli show --run-id RUN_ID --json
```

The installed local raw Futures files currently expose only BTC and ETH for these periods. Two assets are below the evaluator's five-asset minimum. A real multi-asset statistical smoke test and 2025-safe source attestation remain open acceptance gates. The fixed-transform synthetic end-to-end test exercises the runner without reading reserved data.
