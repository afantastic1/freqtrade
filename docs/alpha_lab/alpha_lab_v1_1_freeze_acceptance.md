# Alpha Lab v1.1 Freeze and Agent Admission Acceptance

Date: 2026-10-19  
Branch: `codex/fundamental-data-adapter`  
Audited commit: `a757897321` (`f4d27a9dbf` is an ancestor)  
Freeze status: **RC_BLOCKED**  
Agent admission: **BLOCKED**

This report records an acceptance audit. It does not create a v1.1 freeze baseline or authorize research execution. The original v1.0 `alpha_lab/freeze-manifest.json` is preserved unchanged.

Follow-up P0 work is recorded in [`cs_inference_calibration.md`](cs_inference_calibration.md) and [`five_asset_real_smoke.md`](five_asset_real_smoke.md). Current working-tree changes are not yet a freeze baseline.

## Acceptance summary

| Area | Result | Evidence / blocker |
|---|---|---|
| Full Alpha Lab regression | PASS | Current tree: `311 passed in 22.05s` across `alpha_lab/tests/` and `alpha_lab/calibration/` |
| Existing statistical self-check | PASS | `python -m alpha_lab.stats.selfcheck` exited 0 |
| CS-RankIC golden values | PASS | Synthetic perfect and reversed ranks return +1 and -1 in `test_perfect_and_reverse_cs_rank_ic_golden` |
| Global time split, label availability, train-only model scaling | PASS on synthetic fixtures | Cross-sectional dataset, labels, and Ridge tests pass; no real-panel evidence was produced |
| Five-asset real 1h smoke | PASS AS DIAGNOSTIC | Five actual USDⓈ-M 1h sources; 26,303 bounded rows per asset, 26,303 aligned five-asset sections, labels, global split and evidence identity checked. P-values remain non-confirmatory under the failed calibration. |
| 2025–2026 data-read isolation | AGENT OUTPUT BOUNDARY PARTIAL / BLOCKING | Maintainer approved the trusted process to open mixed-year files then filter. Research Agent file/shell controls prevent direct access and gateway checks dates, but the Python process has host-level file authority. A preliminary maintainer probe printed latest raw rows outside the dev window into the trace; see the smoke report. |
| CS bootstrap calibration | FAIL / BLOCKING | Prespecified 35-case Monte Carlo (5,000 repetitions/case) rejects all compared procedures; see calibration report. |
| SQLite read-only integrity | PASS | `integrity_check=ok`, `foreign_key_check=[]`; database SHA-256 unchanged during the read-only audit. |
| Existing database append-only enforcement | NOT VERIFIED / BLOCKING | Current database has no `cs_evaluations` or `cs_research_decisions` tables. The only listed triggers are `evaluations_parent_insert` and `evaluations_parent_update`; this instance does not demonstrate the current append-only trigger contract. |
| Duplicate identity/reuse and decision immutability | PASS in tests; NOT VERIFIED on real v1.1 store | Synthetic cross-sectional tests check idempotent reuse, changed identity, artifact recovery, append-only conflicts, FK integrity, and decisions. The local database was not migrated or written. |
| OpenCode effective runtime permissions | CONFIG LOADED / STILL BLOCKING | Installed OpenCode `v2.0.29` parsed `.opencode/agents/alpha-researcher.md` through `opencode debug agents`. Effective rules show wildcard deny followed by narrow documented allows. This validates parser loading and order, not OS-level sandboxing. |
| Freeze check / CI | FAIL / BLOCKING | `python -m alpha_lab.freeze` compares against v1.0 and reports changed v1.0 files plus all v1.1 files as untracked frozen files. Existing workflow therefore cannot verify the intended v1.1 baseline. |
| Maintainer approval enforcement | PARTIAL | CODEOWNERS and a freeze workflow exist in the repository. Remote branch protection and required CODEOWNER review cannot be verified or configured from this workspace. |

### P0 follow-up disposition

- The synthetic calibration compares MBB block sizes 4/12/32, HAC lags 4/12/32, and IID t across dependence and panel-size regimes. Its final result and intervals are documented in [`cs_inference_calibration.md`](cs_inference_calibration.md). Since no method passed, existing `cs-time-block-centered-v1` remains diagnostic and no production formula was changed.
- Cross-sectional Research Decisions now accept only exact `INCONCLUSIVE`, `REJECTED`, or `NOT_EVALUABLE` string conclusions under `cs-verdict-gated-v1`; alternate or structured encodings are rejected until calibrated inference is approved. Time-series decisions and history are unchanged.
- Research Agent file permissions were narrowed so panels are no longer readable or searchable directly. Symlinked panel directories and held-out panel timestamps are rejected; public CLI errors are sanitized. This proves neither effective OpenCode runtime enforcement nor physical isolation of mixed-year Feather sources.
- The five-asset smoke ran in an isolated `/tmp` root and used only rows whose open and assumed availability times precede 2025-01-01. It did not touch the user SQLite or write a Research Decision. The approved boundary change allows the trusted process to read mixed-year sources before filtering; this is not physical isolation. A preliminary maintainer probe emitted latest rows outside the development interval into the tool trace, so output-control governance remains a recorded exception.

## Tests and commands

Executed from the repository root:

```text
./.venv/bin/python -m pytest -q alpha_lab/tests/ alpha_lab/calibration/
311 passed in 22.05s

./.venv/bin/python -m alpha_lab.stats.selfcheck
exit 0

./.venv/bin/python -m alpha_lab.freeze
exit 1
changed: alpha_lab/experiment_registry.py
changed: alpha_lab/regime_cli.py
changed: alpha_lab/research_store.py
changed: alpha_lab/runner.py
changed: alpha_lab/stats/audit.py
changed: alpha_lab/stats/verdict.py
changed: alpha_lab/version.py
... additional changed v1.0 files and untracked v1.1 frozen files ...

opencode --version
opencode v2.0.29

opencode debug agents | ./.venv/bin/python -c '... print alpha-researcher permissions ...'
alpha-researcher loaded; wildcard deny precedes narrow docs/candidates/current/gateway allows; no run/panel/raw-market read allow

./.venv/bin/python -m compileall -q alpha_lab
exit 0

git diff --check
exit 0

sha256sum user_data/research/alpha_lab/experiments.sqlite3
f92010447c34d24a293d71693c75950aef45cd4f955310c648929a6d89ad6cf9
```

The current follow-up adds closed-vocabulary decision-gate bypass tests, sanitized history/show output tests, CLI exit-code propagation, pre-read window validation, and calibration window/RNG tests. The bounded five-coin real-data smoke completed in a separate temporary database; its inference remains diagnostic only.

### Additional CS null calibration diagnostic

A read-only synthetic diagnostic used a fixed cross-sectional rank factor, zero-effect labels with independent per-asset AR(1) time series, 80 UTC sections, 8 assets, 60 Monte Carlo replicates per dependence setting, 199 bootstrap replicates per test, and configured block size 4:

| Label serial correlation | Empirical p < 0.05 rate | Median p |
|---:|---:|---:|
| 0.0 | 6.7% | 0.407 |
| 0.5 | 13.3% | 0.340 |
| 0.9 | 48.3% | 0.058 |

This is a diagnostic, not a calibrated benchmark: 60 replicates gives coarse uncertainty and only one block size was used. The strong inflation at rho=0.9 is nevertheless sufficient to reject the current block-size-4 p-value as a freeze-ready significance contract. No inference formula, block setting, threshold, or historical conclusion was changed. A Maintainer must specify and validate a dependence-aware protocol with broader Monte Carlo coverage before using CS bootstrap p-values for supported claims.

## Data boundary and smoke-test disposition

The local `user_data/data/binance/futures/` inventory contains 1h USDⓈ-M Feather files for BTC, ETH, SOL, BNB, and XRP. No prebuilt panel existed. After the user approved the trust boundary, a Maintainer process opened the source files and filtered rows by `open_time` and derived `available_time` before constructing the temporary panel. This did not implement physical isolation. The initial probe printed out-of-window latest rows into the Maintainer trace; the smoke report records this exception. No OHLCV or Catalog source was changed.

The smoke produced valid asset counts, per-time labels and label availability, training cutoff membership, CS-IC/RankIC, bootstrap/HAC diagnostics, and a four-output Holm/BH family. Because inference calibration failed, none of these p-values is confirmatory evidence and no Research Decision was created.

The evaluator uses time sections as bootstrap observations and reports per-section valid asset counts and coverage. Labels are computed per symbol, use the symbol's next open and horizon close, and record `label_available_time`; all training rows passed the global cutoff, and test labels became available before 2025. The smoke confirms `entry_time_feasible_fraction=0`, so this label remains a non-executable prediction proxy.

## SQLite audit

The local SQLite file was opened through SQLite `mode=ro&immutable=1`. Observed values:

```text
SHA-256: f92010447c34d24a293d71693c75950aef45cd4f955310c648929a6d89ad6cf9
integrity_check: ok
foreign_key_check: []
experiments: 24
candidates: 11
evaluations: 19
research_decisions: 11
statistical_reviews: 16
```

Its hash was identical before and after the audit. No v1.1 tables were created and no decision was written. The synthetic tests validate current store behavior on temporary databases; the existing user database remains a legacy instance and cannot demonstrate that behavior without a separately approved backup-and-migration acceptance.

## Agent permissions and admission

The checked-in `.opencode/agents/alpha-researcher.md` uses the OpenCode V2 `permissions` array. The installed `v2.0.29` runtime parsed it; its effective ordered rules apply wildcard deny, then allow research docs, only the current `request.json` and `config.json`, and the exact no-argument gateway command. Historical evidence is available through sanitized gateway DTOs; direct `runs/**` reads are denied. `grep` stays denied because V2 matches its resource against regex text rather than a filesystem path. The gateway accepts fixed transforms and trusted panel IDs; it does not import candidate `factor.py`. Runtime parsing confirms configuration loading, while the OS process still has its normal host authority.

The intended allowlist for a future admitted agent is limited to:

- `relative_momentum`, `relative_volatility`, and `relative_volume` with validated parameters;
- engine-owned same-time transforms `cs_rank`, `cs_zscore`, and `cs_demean` only if a separately versioned, reviewed engine interface exposes them;
- read history/evidence and append a decision through the trusted gateway;
- one bounded research cycle per delegation, with the next cycle selected by the coordinating agent.

At present, the v1.1 CLI exposes only the first three named cross-sectional fixed transforms. Arbitrary Python factors, modified evaluators/statistics, direct database edits, and unverified fundamental PIT features are not admitted. Because the freeze, five-asset smoke, data-read boundary, and calibrated inference gates are open, the Research Agent status is **BLOCKED** for v1.1 execution. Read-only historical inspection and synthetic Maintainer validation may continue under existing controls.

## Required extension interface for new factor types

New factor execution must be proposed as a separate versioned engine extension. A Maintainer change must define a declarative input schema, point-in-time availability contract, deterministic implementation, source and configuration identity, missingness behavior, fixed transform/model parameters, and explicit permitted data fields. It must add golden, leakage, identity/reuse, and security tests; bump `engine_version`, `protocol_version`, or `method_version` as appropriate; preserve previous manifests and evidence; and pass freeze CI plus review by the CODEOWNER. Researchers may submit hypotheses and parameter values only through the approved schema. Arbitrary candidate Python remains disabled until actual OS-level isolation passes adversarial tests.

## RC blockers and next gates

1. Prepare a bounded 2022–2024 five-symbol 1h panel from a source whose read path can prove it never opens 2025+ records. Add a report of valid assets, data coverage, missing bars, and point-in-time Universe membership.
2. Run an end-to-end cross-sectional smoke on that panel and record train/test time boundaries, label availability/purge, per-section asset counts, CS-IC/RankIC, bootstrap, HAC, and campaign correction.
3. Calibrate the CS inference method across independent and multiple serial-dependence regimes, sample sizes, and block-size choices. Record calibration methodology and Golden differences under a new method version before changing inference semantics.
4. Verify effective OpenCode permissions with the installed supported version in a writable runtime, including exact gateway invocation, shell argument rejection, candidate-directory write boundaries, network denial, subagent denial, and arbitrary Python rejection.
5. Snapshot/migrate a disposable copy of the existing SQLite database and verify append-only triggers, identity constraints, duplicate reuse, concurrent writes, and unchanged historical decisions. Do not migrate the live database during acceptance.
6. Add a distinct v1.1 manifest and update CI/CODEOWNERS only after the acceptance gates above pass. Retain the v1.0 manifest and require repository branch protection plus CODEOWNER review to be configured by an administrator.

Until these gates pass, Alpha Lab v1.1 remains **RC_BLOCKED**, and no formal Research Agent execution is authorized.
