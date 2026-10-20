# Alpha Lab next steps: return to factor research

This is a capability inventory and priority list, not implementation of a new model or strategy.

## Current capability snapshot

| Capability | Current state | Practical boundary |
|---|---|---|
| Agent proposes factor hypotheses | Partial | The bounded `alpha-research` gateway supports one declared cycle and historical evidence review. Agent admission is still limited by the frozen-evidence/data gates. |
| Factor expression construction | Partial | Cross-sectional Agent input supports only engine-owned `relative_momentum`, `relative_volatility`, and `relative_volume` transforms. Arbitrary Python `factor.py` is disabled for unattended use pending OS isolation. |
| Multi-timeframe access | Partial | Time-series config accepts 5m / 15m / 1h / 4h; each run declares one timeframe. Cross-sectional panel config is also one timeframe per run. No synchronized multi-timeframe feature join is exposed to the Agent. |
| Time-series factor evaluation | Present, restricted | IC/RankIC, fold metrics, logistic incremental metrics, bootstrap diagnostics and cost sensitivity exist. Legacy arbitrary-candidate execution is Maintainer-only until a sandbox is proven. Formal inference is closed by the method registry. |
| Cross-sectional factor evaluation | Present, diagnostic | Per-section IC/RankIC, rank spreads, turnover, cost deductions, Ridge and panel evidence exist. Current inference status is `BLOCKED`; no formal significance claim is allowed. |
| Factor deduplication / correlation | Partial | Factor report and incremental evaluation compare correlation/redundancy for evaluated candidates; campaign-wide search history still needs stronger family identity and selection accounting. |
| Multi-factor model training | Present | Time-series logistic and cross-sectional Ridge paths exist with train-only fitting/scaling and bounded feature sets. No new model is proposed here. |
| Walk-Forward | Present | Rolling monthly train/test folds, label availability checks and purge logic exist. Repeated candidate tuning is not separated by a nested search plus untouched final test. |
| Trading costs | Partial | Cross-sectional spread cost/turnover and time-series cost sensitivity/vectorized execution accounting exist. These are research estimates, not exchange-accurate fills or a production execution model. |
| Strategy backtest | Partial | Alpha Lab has local execution accounting and Freqtrade backtesting exists elsewhere in the project; the factor research gateway does not automatically turn candidates into audited Freqtrade strategies. |
| Experiment history / reproducibility | Present with open gates | Append-only stores, run manifests, source/config/data fingerprints, method versions and artifact checks exist. The v1.1 manifest now covers method registry and Agent/CI policy files. |

## Priority order

1. **Keep method scope frozen.** Use only descriptive PASS metrics and multiplicity adjustment on valid p-values. Treat all current MBB/HAC inference outputs as `LEGACY`, `BLOCKED`, or diagnostic. Do not spend another cycle tuning `estimated_phi`, lag lengths, block lengths, or acceptance thresholds.
2. **Close the research data boundary.** Before agent campaigns, provide a bounded development panel whose read path proves it cannot open 2025+ rows; add a compact coverage, missing-bar, universe-membership, and point-in-time availability report. The previous mixed-year source path was not physical isolation.
3. **Admit only fixed-transform exploratory cycles.** If the bounded gateway, panel, and decision gate are operational, permit one hypothesis/one candidate cycle using the current fixed transforms. Label the evidence diagnostic and prevent formal `SUPPORTED` / `REJECTED` output while no inference method is PASS.
4. **Improve factor research through schema additions.** Prioritize a small set of interpretable transforms and precise data contracts (availability, missingness, universe, parameters, source hash). Do not permit arbitrary Agent code. Any new transform requires a Maintainer change, leakage tests, identity checks, and owner review.
5. **Strengthen selection accounting and validation.** Make research families explicit across repeated candidates and model settings; use nested Walk-Forward or a truly untouched final period before making selection-adjusted claims. Keep the repeatedly used 2022–2024 sample labeled development data.
6. **Connect promising factors to realistic strategy validation.** After factor-level evidence, test portfolio construction, turnover, fees, slippage, funding and execution using Freqtrade's backtest path. Do not equate IC or model AUC with profitable net trading.

## Agent admission decision

The repository has a bounded Agent path for fixed cross-sectional transforms, but it is not general-purpose autonomous factor mining. Do not enable broad unattended campaigns now: the inference registry has no `PASS` inference method, the documented data-read isolation gate remains open, and arbitrary Python remains disabled pending OS sandbox verification. A single exploratory fixed-transform cycle can be considered only when its panel boundary and gateway checks are satisfied; its conclusion must remain diagnostic.
