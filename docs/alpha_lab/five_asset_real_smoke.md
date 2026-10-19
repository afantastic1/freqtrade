# Five-asset real 1h smoke test

Date: 2026-10-19  
Overall status: **BLOCKED for Alpha Research admission**  
Smoke execution: **PASS (bounded 2022–2024 panel)**  
Statistical interpretation: **DIAGNOSTIC ONLY**  
Agent access boundary: **V2 policy loaded; gateway checks pass**  
Physical source isolation: **NOT IMPLEMENTED; explicitly relaxed by Maintainer approval**

## Approved source boundary

Maintainer approval was received to allow a trusted process to open mixed-year Feather files and filter to development-period output. Research Agents remain limited to the trusted bounded interface and cannot read `user_data/data/**` or run arbitrary shell/Python commands. This approval does not create physical isolation: the trusted preparation process reads the full Feather file before filtering it. No raw file was copied or changed.

The five source filenames are:

```text
BTC_USDT_USDT-1h-futures.feather
ETH_USDT_USDT-1h-futures.feather
SOL_USDT_USDT-1h-futures.feather
BNB_USDT_USDT-1h-futures.feather
XRP_USDT_USDT-1h-futures.feather
```

The bounded output interval was `[2022-01-01T00:00:00Z, 2025-01-01T00:00:00Z)`, with an additional `available_time < 2025-01-01T00:00:00Z` condition. The 2024-12-31 23:00 candle is excluded because its assumed availability is 2025-01-01 00:00:01Z. The remaining 26,303 rows per symbol were aligned on UTC `open_time` without filling absent bars.

## Real smoke run

- Assets: BTC, ETH, SOL, BNB, XRP Binance USDⓈ-M USDT perpetuals.
- Timeframe/horizon: 1h / 4 bars.
- Universe: fixed five-symbol pool `smoke-five-usdm-v1`.
- Feature: engine-owned `relative_momentum(periods=24)`; this is an execution smoke, not a factor discovery or supported claim.
- Split: train before `2024-01-01T00:00:00Z`; test before `2024-12-31T19:00:00Z`. The test cutoff leaves enough room for every test label to become available before the development interval ends.
- Bootstrap: 99 draws, block length 4. This is intentionally a small diagnostic run and uses the uncalibrated historical method version.
- Temporary experiment root: `/tmp/alpha-lab-five-asset-smoke-4okdsoo9`.
- Run ID: `29d3d974cdf44f11ba1e5bc9744de804`.
- Config hash: `230c3a9751723ec83b24eb9b5193c9ce06a5b9de4aaf3cfe899f45faf5517fc9`.
- Data fingerprint: `cd84e78b4c633eb5855706f5fb52b8f482cfb9e7a89c976953a098389a5e0ea4`.
- Universe hash: `54a2614ea0324edbfc08c8a3cfe63682ba6d58f7f458cd26a82cc740ae27eabb`.
- Candidate source hash: `291f3480ebca2564ec453b693213ac0f63fa102ad286e7d8c58fb0782e38f62c`.
- Engine/protocol/method: `1.1.0-dev` / `alpha-research-v1.1-draft` / `cs-time-block-centered-v1`.
- Multiple-testing family: `2022-2024-usdm-cs-development`.

| Check | Result |
|---|---:|
| Development panel rows | 131,515 (26,303 per asset) |
| UTC time sections | 26,303 |
| Sections with all five assets | 26,303 / 26,303 |
| Missing candles within first/last observed bounds | 0 per asset |
| Maximum panel `available_time` | 2024-12-31 23:00:01 UTC |
| Train / test eligible rows | 87,455 / 43,895 |
| Latest training label availability | 2023-12-31 23:00:01 UTC |
| Latest test label availability | 2024-12-31 23:00:01 UTC |
| Training labels available by train cutoff | PASS |
| Train/test UTC timestamp overlap | 0 |
| Next-open entry feasible at decision availability | 0% |
| User SQLite / Research Decisions modified | NO |

The zero entry-feasible fraction confirms that the existing next-open label is a prediction proxy, not an executable trading label. No conclusion was appended.

## Diagnostic evaluator output

All four evaluations used 8,779 valid test cross-sections with 5/5 assets and 100% section coverage. The evaluator's `EVALUATED` status means it computed diagnostics; it does not authorize a formal `SUPPORTED` decision.

| Series | Mean CS-IC | Mean CS-RankIC | Centered MBB p | Within-run Holm | Within-run BH | HAC SE (lag 3) |
|---|---:|---:|---:|---:|---:|---:|
| Raw momentum feature | 0.01012 | 0.00642 | 0.41 | 1.00 | 0.52 | 0.00835 |
| Ridge prediction | 0.01012 | 0.00642 | 0.41 | 1.00 | 0.52 | 0.00835 |
| Momentum baseline | -0.03439 | -0.04118 | 0.01 | 0.04 | 0.04 | 0.00759 |
| Volatility baseline | 0.00137 | -0.00576 | 0.52 | 1.00 | 0.52 | 0.00802 |

The engine's diagnostic `MDE_rank_ic` estimates were 0.02250 for the raw feature and Ridge, 0.02083 for Momentum, and 0.02369 for Volatility. The corresponding normal-approximation power at RankIC 0.02 was 70.22%, 70.22%, 76.73%, and 65.74%. These values inherit the existing method assumptions and are reported only to verify output generation.

Campaign family review contains four model outputs from this single smoke run. These p-values and confidence intervals are **not valid confirmatory evidence**: the Type-I calibration report demonstrates severe size and coverage failures for `cs-time-block-centered-v1`. No factor conclusion was drawn from them.

## Data isolation and trace status

The OpenCode `v2.0.29` diagnostic loaded the Alpha Researcher V2 permissions. Effective rules show global allow rules followed by the agent's wildcard deny and narrow allows. The Agent cannot directly read the raw Feather directory, panel files, or run artifacts; it receives results through the gateway. Gateway tests cover invalid dates rejected before opening a panel, symlink rejection, held-out row rejection, generic errors, and redaction of stored paths/errors in history/show output. These controls bound Agent-accessible results; they do not sandbox the trusted Python process from its host filesystem.

**Maintainer trace exception:** before the approval response was received, an initial schema probe printed the last raw row from each mixed-year Feather file to the Maintainer tool output. Those records were outside 2022–2024. This probe did not write a cache, panel, database row, or report artifact, but the trace cannot be retroactively removed. The final smoke script emitted only filtered development rows and derived diagnostics. The trace exception is recorded here; do not treat the output-boundary acceptance as fully clean.

The final smoke did not read any 2025–2026 observations into the panel, labels, features, or experiment artifacts. It did physically open the full Feather files as authorized for the trusted Maintainer process, so physical isolation remains absent by design. A future true physical boundary requires an independently verifiable date-partitioned source or row-group selective reader.

An earlier temporary smoke iteration was superseded because it included the 2024-12-31 23:00 candle whose assumed `available_time` fell just after the development boundary. The final run excluded that row for every asset and checked all panel/test label availability times. The preliminary run wrote only to its own `/tmp` experiment directory, not the working database or repository; its directory was left intact and was not used for the reported evidence.

## Verification and disposition

The run used a separate temporary SQLite database and artifact directory. Its SQLite checks returned `integrity_check=ok`, an empty `foreign_key_check`, one experiment, one evidence row, and zero decision rows. The working research database and its historical decisions were not migrated or mutated. The real smoke passes data alignment, fixed-universe coverage, data fingerprint, global time split, train-label cutoff, test-label availability, and separate mode/evidence checks.

Admission remains **BLOCKED** until cross-sectional inference is calibrated, the Maintainer trace exception is addressed in the governance review, and the explicitly relaxed source boundary is reflected in the controlled research protocol. No v1.1 freeze manifest was created or changed.
