# Cross-sectional inference calibration

Date: 2026-10-19  
Status: **BLOCKED**  
Production method: `cs-time-block-centered-v1` (diagnostic only; unchanged)  
Decision policy: `cs-verdict-gated-v1`  
Calibration design: `cs-inference-mc-prespecified-v1`

## Scope and design

This synthetic calibration does not read market data or modify production inference. The scenario grid, method settings, seed, and acceptance limits are recorded in [`cs_inference_calibration.py`](../../alpha_lab/calibration/cs_inference_calibration.py), with cell-level output in [`cs_inference_calibration_results.json`](cs_inference_calibration_results.json). The design was fixed for this recorded run; it is not represented as a preregistration predating the implementation.

Command:

```text
./.venv/bin/python -m alpha_lab.calibration.cs_inference_calibration \
  --repetitions 5000 --bootstrap-replicates 399 --seed 20261019 \
  --output /tmp/cs_inference_calibration_final.json
```

The 35-case matrix has 29 zero-effect scenarios and six positive-effect scenarios. It varies AR(1) persistence (rho 0, 0.5, 0.9), 5/10/25 assets, 80/250 UTC sections, common shocks, GARCH-like heteroskedasticity, and overlapping target windows of 1/4/12 sections. Each cell uses 5,000 independently generated panels. MBB uses 399 replications per test with independently generated resampling indices for each Monte Carlo panel.

Compared procedures are the centered percentile moving-block bootstrap at block lengths 4/12/32, Bartlett Newey-West HAC at lags 4/12/32, and IID one-sample t as a misspecified reference. The MBB procedure centers resampled means at the observed mean and tests the observed mean against that centered distribution. HAC uses a Student t reference with `T - lag - 1` degrees of freedom. These are comparisons, not candidate production settings.

Acceptance limits were fixed before this run:

- For every null scenario and procedure, the Bonferroni simultaneous Wilson upper bound for empirical Type-I error must be at most 6% (familywise confidence 95%).
- For every null and alternative coverage cell, the Bonferroni simultaneous Wilson interval must lie wholly within 92%–98% (familywise confidence 95%).
- Power and its ordinary 95% Wilson interval are reported, but cannot compensate for size or coverage failure.

No threshold or block length was selected after seeing scenario-level outcomes.

## Results

**No compared procedure passes.** Final output: 35 scenarios × 7 procedures, 5,000 repetitions per scenario. Exact simultaneous intervals and every cell are preserved in the JSON artifact.

| Procedure | Max null Type-I | Simultaneous upper bound | Null coverage range | Alternative coverage range | Mean power |
|---|---:|---:|---:|---:|---:|
| MBB block 4 | 29.92% | 32.38% | 67.64–94.26% | 70.74–90.54% | 79.43% |
| MBB block 12 | 19.56% | 21.73% | 74.68–93.02% | 75.72–89.76% | 74.49% |
| MBB block 32 | 24.48% | 26.81% | 61.92–89.32% | 63.92–85.78% | 75.36% |
| HAC lag 4 | 27.62% | 30.03% | 72.38–94.84% | 74.98–91.66% | 78.44% |
| HAC lag 12 | 20.38% | 22.58% | 79.62–93.96% | 76.56–90.70% | 75.18% |
| HAC lag 32 | 25.66% | 28.02% | 74.34–91.52% | 73.82–88.84% | 76.42% |
| IID t | 55.80% | 58.39% | 44.20–95.46% | 47.42–92.86% | 87.93% |

The exact output contains all 5,000-repetition cell counts and simultaneous Wilson intervals. Review corrected two implementation defects before this recorded result was used: MBB index draws are independent across Monte Carlo panels, and horizon windows include exactly the requested number of future rows (including `horizon=1`). The coverage family confidence is 99.9796% (Bonferroni over all 245 scenario-method cells); the Type-I family uses this same more conservative confidence for all metrics. Every Type-I maximum exceeds the 6% acceptance ceiling and every method misses coverage bounds in at least one scenario. The earlier rho 0.9 diagnostic's 6.7%/13.3%/48.3% pattern is consistent with this larger simulation.

## Decision gate and method version

Cross-sectional `SUPPORTED` decisions remain disabled. The ResearchStore now accepts only exact `INCONCLUSIVE`, `REJECTED`, or `NOT_EVALUABLE` string conclusions while this gate is closed. Dict conclusions and alternate spellings are rejected. This policy is `cs-verdict-gated-v1`; it does not alter time-series decisions or historical records.

The production inference formula is unchanged because no replacement has passed the calibration contract. Existing evidence remains identified by `cs-time-block-centered-v1` and must retain its diagnostic interpretation. A future successful replacement must receive a new `method_version`, preserve old results, and include a documented theoretical rationale and Golden Test deltas before enabling formal `SUPPORTED` conclusions.

## Verification

The Monte Carlo used seed `20261019`, 5,000 repetitions per scenario, and 399 bootstrap replicates per MBB test. The artifact is reproducible with the command above; its calibration outputs are independent of the production evaluator. No user SQLite, market file, history, or Research Decision is used or changed by this synthetic calibration.

```text
./.venv/bin/python -m pytest -q alpha_lab/calibration/test_cs_inference_calibration.py
5 passed in 0.73s

./.venv/bin/python -m pytest -q alpha_lab/tests/ alpha_lab/calibration/
311 passed in 22.05s

./.venv/bin/python -m compileall -q alpha_lab
exit 0

git diff --check
exit 0
```

The result JSON in this directory contains the full scenario-by-procedure Wilson intervals, including the simultaneously corrected alternative coverage intervals.
