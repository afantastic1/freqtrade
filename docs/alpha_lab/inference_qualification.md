# Alpha Lab inference qualification

Status: **no inferential method qualified; v1.1 admission remains `RC_BLOCKED`.**  
Protected core left unchanged: `alpha_lab/stats/**`, `alpha_lab/calibration/**`, Registry, verdict policy, evaluator, and freeze manifests.

## Methods and current scope

The calibration report fixes a single cross-sectional RankIC estimand: the mean of UTC-section RankIC values. One panel section is one time observation. The reviewed procedures are not interchangeable with asset-row IID inference.

| Registry method / candidate | Qualification status | Scope allowed now | Failure evidence / attribution |
|---|---|---|---|
| `cs-time-block-centered-v1` (moving-block bootstrap; candidates block 4/12/32) | **RESTRICTED**; Registry remains **BLOCKED** for inference | Point RankIC and bootstrap outputs may be displayed as diagnostics only. No inferential p-value, confidence claim, `SUPPORTED`, or `REJECTED` claim. | All three settings fail Type-I and interval coverage in the existing fixed grid. Worst is block 4: Type-I 29.92%, familywise upper bound 32.38%; 67.64% minimum null coverage. The common worst cell is `rho=.9`, 25 assets, 80 sections, no common shock, no heteroskedasticity, horizon 1. No pass can be obtained by picking a block after observing these outcomes. |
| `hac-bartlett-calibration-v1` (HAC lags 4/12/32) | **RESTRICTED**; Registry remains **BLOCKED** for inference | HAC standard errors may be shown as diagnostics only. No formal p-value or confidence claim. | All three lags fail. Worst is lag 4: Type-I 27.62%, familywise upper bound 30.03%; minimum null coverage 72.38%. Worst cell: `rho=.9`, 25 assets, 80 sections, no common shock, no heteroskedasticity, horizon 1. Lag 12 has the lowest observed maximum Type-I among these settings (20.38%) but still fails; it is not a selected replacement. |
| `estimated_phi` | **BLOCKED** | None. Do not use or reconstruct. | Registry cites a historical 16.20% Type-I result, but this checkout has no implementation, scenario matrix, seed, or raw output. Attribution among implementation error, estimator error, finite-sample error, and model misspecification is **not identifiable** from current evidence. |
| Calibration reference `iid_t` (not a production registry method) | **BLOCKED** | No serially dependent RankIC inference. | Type-I reaches 55.80% (simultaneous upper bound 58.39%); minimum null coverage 44.20%. Worst cell: joint stress, `rho=.9`, 10 assets, 250 sections, common shock .5, heteroskedasticity, horizon 12. This demonstrates the independence assumption failure in the tested dependence regimes. |
| `scipy-pearson-point-v1`, `scipy-spearman-point-v1` | **QUALIFIED**, descriptive role only | Point coefficient only; may be labeled descriptive/exploratory. | These are point-estimate implementations. Their `PASS` registry role does not qualify an interval or p-value and does not repair inference from another method. |
| `holm-bonferroni-v1`, `scipy-bh-v1`, `scipy-by-v1` | **RESTRICTED**, adjustment role only | Adjust already-valid marginal p-values over a declared family. | Multiplicity adjustment does not fix invalid marginal p-values. Current blocked CS p-values are not made valid by Holm/BH/BY. |
| `paired-mbb-centered-null-v3`, `prereg-a-v3`, `prereg-b-v2-centered-null`, `ts-rankic-mbb-horizon-v1` | **RESTRICTED** for historical reproduction; Registry remains **LEGACY** | Reproduce old Evidence with its original method identity; no new formal inference. | The registry records uncalibrated/custom bootstrap inference. Existing historical conclusions retain their original interpretation and are not upgraded. |

## Failure classification

The existing Monte Carlo harness review records two implementation defects that were fixed before the published 35-scenario result: MBB resampling indices needed independence across Monte Carlo panels, and the forward-window slice needed to contain exactly the declared horizon, including horizon one. This is a harness correction, not evidence that the production evaluator's inference was repaired. The present report and registry use the post-correction artifact.

After those corrections, high empirical rejection rates and low interval coverage remain. They are not explained by Monte Carlo estimation error alone: each cell has 5,000 repetitions, and the reported familywise Wilson bounds remain far beyond the pre-set acceptance limits. The 80-section high-persistence cell is a finite-sample stress; the joint stress also includes overlapping targets, common shocks, and heteroskedasticity. MBB block lengths and HAC bandwidths are finite tuning parameters and are not valid uniformly across these dependence regimes. This evidence establishes failure over the tested scope, not impossibility of every bootstrap/HAC design under every regularity condition.

The `estimated_phi` method is different: its implementation and raw calibration are absent, so even a failure-mode attribution cannot be reproduced. It remains blocked for lack of auditable evidence, not because a new implementation was guessed and rejected.

## Fixed one-shot replication registration

This is a reproduction of an already-known negative calibration, **not a blind first qualification**. The earlier outcomes are retained and cannot be displaced by this run. The purpose is to check that the existing frozen scenario generator produces the same broad failure under a new seed. It does not introduce or modify a method.

Before running the replication, these settings are fixed:

- Scenario generator: the existing `calibration_matrix()` in `alpha_lab/calibration/cs_inference_calibration.py`, all 35 cells, without filtering. It covers `rho` 0/.5/.9, 5/10/25 assets, 80/250 sections, common shocks, heteroskedasticity, and horizons 1/4/12; both null and positive-effect alternatives remain included.
- Every existing candidate: centered MBB blocks 4/12/32, Bartlett HAC lags 4/12/32, and IID t reference. No new candidate, lag, block size, scenario, seed retry, or threshold search.
- Repetition settings: 5,000 panels per scenario, 399 MBB resamples per panel, seed `20261020`, alpha .05, nominal interval .95.
- Type-I gate: in every null cell, Bonferroni simultaneous Wilson upper bound `<= .06`.
- Coverage gate: Bonferroni simultaneous Wilson interval must lie within `[.92, .98]` in every applicable null and alternative cell.
- Power is reported with ordinary Wilson intervals and cannot offset a Type-I or coverage failure.
- Stop rule: run this exact replication once. Any failure preserves the method as blocked; no tuning/re-run. Even a replicate-only pass cannot erase the earlier failed campaign or qualify the existing method version.

The prior result artifact already fixes these same gates and reports all scenarios and methods. It explicitly says the design was not preregistered before implementation. Therefore the replication above is honestly labeled as registered before its own run, while the historical study remains retrospective. Neither is misrepresented as preregistered method development.

## Calibration artifacts and reproducibility

Historical fixed-result report: [`cs_inference_calibration.md`](cs_inference_calibration.md). Full original cell output: [`cs_inference_calibration_results.json`](cs_inference_calibration_results.json), retained unchanged. The one-shot replication command was:

```text
.venv/bin/python -m alpha_lab.calibration.cs_inference_calibration \
  --repetitions 5000 --bootstrap-replicates 399 --seed 20261020 \
  --output /tmp/cs_inference_mc_seed_20261020.json
```

The one-shot replication completed on 2026-10-20 with 35 scenarios, 5,000 repetitions, 399 bootstrap replicates, and seed `20261020`. Output SHA-256: `fe7a92da65e09dfd8b846d52a736b0a3aa1c022c5c9cec92200c6925c16f36be` (temporary output at `/tmp/cs_inference_mc_seed_20261020.json`). The independent replicate reproduces the failure; no candidate passes both gates:

| Candidate | Max null Type-I | Simultaneous upper bound | Null coverage range | Alternative coverage range | Gate result |
|---|---:|---:|---:|---:|---|
| MBB block 4 | 28.98% | 31.42% | 68.44–94.38% | 70.00–90.68% | **BLOCKED** |
| MBB block 12 | 19.06% | 21.21% | 75.12–92.94% | 76.24–88.80% | **BLOCKED** |
| MBB block 32 | 23.88% | 26.19% | 63.04–89.00% | 64.34–85.12% | **BLOCKED** |
| HAC lag 4 | 25.94% | 28.31% | 74.06–94.72% | 74.12–91.74% | **BLOCKED** |
| HAC lag 12 | 19.98% | 22.16% | 80.02–94.08% | 80.48–89.86% | **BLOCKED** |
| HAC lag 32 | 25.26% | 27.61% | 74.74–91.38% | 75.50–87.52% | **BLOCKED** |
| IID t reference | 55.22% | 57.81% | 44.78–95.30% | 46.86–93.10% | **BLOCKED** |

The worst MBB/HAC size cell is again the high-persistence, small-time-sample scenario (`rho=.9`, 25 assets, 80 sections, horizon 1); MBB block 4 and HAC lag 4 also have severe joint-stress failures. IID t is worst under the joint stress (`rho=.9`, 10 assets, 250 sections, common shock .5, heteroskedasticity, horizon 12). One IID null cell whose familywise upper bound is under 6% does not override failures in the other cells. No lag, block, scenario, or acceptance rule was changed after results. The registry remains unchanged.

## Decision and replacement

No current method is qualified for inferential cross-sectional RankIC research. The restricted alternative is to report per-section IC/RankIC point summaries, temporal sign/stability summaries, and incremental point comparisons as **descriptive diagnostics**, with no inferential language. Do not substitute adjusted p-values, IID standard errors, or an unreviewed external method. A future inference candidate needs its own version and a new, independently reviewed calibration contract before it can be considered for admission.
