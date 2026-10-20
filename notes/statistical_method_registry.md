# Alpha Lab statistical method registry

Registry version: `alpha-stat-method-registry-v1`  
Status values: `PASS`, `BLOCKED`, `UNSUPPORTED`, `LEGACY`  
Formal verdict rule: only an entry with `status=PASS` and `role=inference` may produce formal `SUPPORTED` or `REJECTED` results. Adjustment and descriptive methods do not qualify as inference methods.

## Approved / usable methods

| Method ID | Status | Role | Allowed use and conditions |
|---|---|---|---|
| `scipy-pearson-point-v1` | PASS | descriptive | Pearson IC point estimate on aligned nonconstant inputs. No serially robust p-value is implied. |
| `scipy-spearman-point-v1` | PASS | descriptive | Spearman RankIC point estimate on aligned nonconstant inputs. No serially robust p-value is implied. |
| `scipy-bh-v1` | PASS | adjustment only | SciPy Benjamini–Hochberg adjustment when marginal p-values are valid, family membership is fixed, and independence/positive dependence assumptions are defensible. |
| `scipy-by-v1` | PASS | adjustment only | SciPy Benjamini–Yekutieli adjustment for arbitrary dependence among valid marginal p-values; more conservative than BH. |
| `holm-bonferroni-v1` | PASS | adjustment only | Existing standard step-down Holm implementation, with known-value tests; requires valid marginal p-values and a declared family. |

There is currently **no PASS inference method**. Thus no method is currently permitted to produce formal statistical `SUPPORTED` or `REJECTED` verdicts. Descriptive factor research, point metrics, and development comparisons may continue with their limitations stated.

## Restricted methods

| Method ID | Status | Scope | Reason / permitted use |
|---|---|---|---|
| `paired-mbb-centered-null-v3` | LEGACY | Paired time-series ΔAUC / Δlogloss / ΔBrier | Keep old implementation and saved results for historical reproduction and diagnostic intervals. Do not use for new formal labels. |
| `prereg-a-v3` | LEGACY | Time-series regime and incremental review | Its verdict route uses the legacy paired MBB; historical rows remain unchanged. |
| `prereg-b-v2-centered-null` | LEGACY | Non-OHLCV time-series RankIC | Custom row-block bootstrap lacks accepted calibration for formal inference. Diagnostics only. |
| `ts-rankic-mbb-horizon-v1` | LEGACY | Pooled time-series RankIC | Fixed horizon blocks do not establish dependence-robust inference. Diagnostics only. |
| `cs-time-block-centered-v1` | BLOCKED | Cross-sectional section-level RankIC | The recorded calibration failed its prespecified Type-I error and coverage requirements. Do not use for formal verdicts. |
| `hac-bartlett-calibration-v1` | BLOCKED | Tested section-level Bartlett HAC settings | The recorded calibration failed. No lag setting from that run is approved. HAC SE output is diagnostic only. |
| `estimated_phi` | BLOCKED | Historical AR(1) calibration claim | The supplied history reports 16.20% Type-I error. Source and raw artifact are absent from this checkout; do not run or use it. |
| `n-eff-design-horizon-v1` | LEGACY | `n / horizon` | Label-window design count only; not a general effective sample size. |
| `newey-west-vif-v1` | LEGACY | Custom lag-truncated variance inflation | Diagnostic only; not a robust standard error or formal p-value. |

## Not implemented / unsupported

| Method ID | Status | Scope |
|---|---|---|
| `statsmodels-newey-west` | UNSUPPORTED | No installed/declarative statsmodels adapter or validated project protocol. |
| `linearmodels-fama-macbeth` | UNSUPPORTED | No implementation or panel-regression estimand contract; not a CS RankIC replacement. |
| `white-reality-check` | UNSUPPORTED | No implementation or declared strategy-search family. |
| `hansen-spa` | UNSUPPORTED | No implementation or declared strategy-search family. |
| Nested Walk-Forward selection correction | UNSUPPORTED | Existing walk-forward folds do not supply a nested search plus untouched final-test protocol. |

## Version and history rules

- Method IDs are append-only identifiers. A formula or inferential semantics change gets a new ID; the old ID remains in the registry as `LEGACY` or `BLOCKED`.
- Existing run summaries, evidence rows, Research Decisions, and method versions are not rewritten.
- `classify_formal_result` is the formal decision boundary. Unknown IDs, adjustment-only IDs, and every non-`PASS` inference method are forced to `INCONCLUSIVE`; the combined mechanism verdict is gated too.
- `STATISTICAL_REGISTRY_VERSION` is included in research identity and the v1.1 freeze manifest, so changing status policy invalidates reuse and requires owner review.
- No status can be promoted by a research Agent. A Maintainer must add the method, tests, explicit assumptions and scenario scope, update the method version, and pass the frozen workflow.
