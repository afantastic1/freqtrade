# Alpha Lab statistical freeze report

Date: 2026-10-20  
Engine/protocol: `1.1.0-rc1` / `alpha-research-v1.1`  
Method registry: `alpha-stat-method-registry-v1`  
Release status: **diagnostic research freeze established; confirmatory inference and general Agent mining remain blocked**

## Frozen contract

- Formal `SUPPORTED` and `REJECTED` verdicts pass through the registered method gate. An unknown method, `LEGACY`, `BLOCKED`, `UNSUPPORTED`, or adjustment-only method becomes `INCONCLUSIVE`. The final combined mechanism verdict is gated too.
- No current inference method has status `PASS`. The cross-sectional evaluator writes `inference_status=BLOCKED`; its existing store admission gate still does not admit formal supported claims.
- SciPy performs BH and BY correction. Holm remains the tested standard step-down implementation. A correction method never makes an invalid input p-value valid.
- Existing bootstrap, HAC, RankIC uncertainty, effective-sample, experiment, and decision records remain unchanged. Old method identifiers remain available as legacy/blocked reproductions.
- Research identity includes the registry version; time-series result fingerprints cover inference and verdict source files. The new v1.1 manifest also hashes the Research Agent policy, research skill, CODEOWNERS, CI workflow, and the four handoff notes. The original `freeze-manifest.json` remains the v1.0 baseline.
- Existing OpenCode policy is default-deny, permits only bounded candidate request/config edits and an exact trusted gateway invocation, and denies edits to Alpha Lab infrastructure. The new manifest freezes that policy and the CI/CODEOWNERS contract. Remote branch-protection enforcement remains an administrator setting and was not verified from this checkout.

## Verification evidence

Commands run after the changes:

```text
.venv/bin/python -m pytest -q alpha_lab/tests/ alpha_lab/calibration/
324 passed in 20.46s

.venv/bin/python -m compileall -q alpha_lab
exit 0

git diff --check
exit 0
```

The focused red/green checks also established that the new BY/SciPy test failed before implementation, then passed; the formal method-gate test likewise failed before the registry was implemented. Tests compare BH and BY output against SciPy and verify that blocked/unknown methods and a blocked combined mechanism verdict cannot produce formal labels.

No new Monte Carlo search was run. The prior cross-sectional calibration result and all its stored scenarios remain unchanged and diagnostic. No research SQLite database, market data, original calibration result, or trading configuration was opened for writing.

## Final method disposition

| Research question | Formal status | Current permitted work |
|---|---|---|
| Time-series Pearson/Spearman IC point estimate | PASS, descriptive only | Report point estimates and sample context; no IID p-value as confirmatory evidence. |
| Time-series IC / paired metric significance | No PASS method; existing MBB is LEGACY | Exploratory evaluation and historical reproduction only. Formal TS verdicts are gated to INCONCLUSIVE. |
| Cross-sectional mean section RankIC inference | BLOCKED | Descriptive per-section metrics and cost diagnostics only. The recorded MBB/HAC configurations failed calibration. |
| `estimated_phi` AR(1) inference | BLOCKED | Do not run or use; source/artifact absent, and supplied Type-I rate is 16.20%. |
| BH / BY / Holm | PASS as p-value adjustment only | Use only on valid marginal p-values and a predeclared family; BY is available when arbitrary dependence needs conservative control. |
| Fama–MacBeth, SPA, White Reality Check | UNSUPPORTED | No implementation is present; do not imply these methods are available. |
| Effective sample size estimates | LEGACY diagnostics | `n/horizon` and custom VIF do not replace robust standard errors. |

## Answers to required questions

- **Which statistics can be used formally?** Descriptive SciPy IC/RankIC point estimates and SciPy BH/BY or Holm p-value adjustment under their stated conditions. There is no approved inferential method with status `PASS`, so adjusted values currently cannot support formal significance.
- **Which methods remain blocked?** Cross-sectional MBB/HAC significance and `estimated_phi`. Legacy time-series MBB is not available for new formal verdicts. Fama–MacBeth, SPA, and White Reality Check are unsupported because they are not implemented.
- **Which custom implementation was replaced?** Benjamini–Hochberg now delegates to SciPy. BY is exposed through SciPy as a conservative dependence-robust adjustment. Holm remains a small, known-value-tested standard implementation because it has no current declared library implementation.
- **Is time-series research still impeded?** Confirmatory significance is blocked. Descriptive factor construction, IC/RankIC, walk-forward prediction, model comparison, and cost sensitivity can continue as development research without formal claims.
- **Is cross-sectional research still impeded?** Confirmatory inference is blocked by the failed calibration and the unresolved physical isolation of mixed-year source files. Fixed-transform diagnostic runs exist but do not authorize formal conclusions.
- **Can Agent automatic factor mining start?** No general unattended mining. The bounded fixed-transform gateway exists, but arbitrary Python remains disabled, formal inference has no PASS method, and the held-out data read boundary remains an open gate. At most, a bounded exploratory cycle may run when its approved panel/gateway checks pass, with diagnostic-only conclusions.
- **Is the statistics infrastructure freeze-ready?** Yes, as a versioned diagnostic-only infrastructure baseline with an empty formal-inference whitelist and a fail-closed verdict boundary. It is not a release approval or a claim that statistical inference has passed. The v1.1 code/agent-policy freeze check must pass against `alpha_lab/freeze-manifest-v1.1.json`.

## Remaining blockers

1. The original `estimated_phi` DGP, estimator, statistic, and raw output are absent. A maintainer must supply those exact artifacts before any code-level root-cause statement is possible. Until then, the method remains blocked; no search is warranted.
2. A validated mature inference adapter is still needed for a specified time-series estimand before formal TS verdicts can be enabled. The project currently has no `statsmodels` or `arch` dependency; this pass did not add one without a concrete, testable adapter and scope.
3. Cross-sectional formal inference stays blocked. Do not select a block size or lag from the failed calibration output.
4. Agent admission needs a physically bounded development panel and verified process/runtime restrictions. The prompt permission layer alone is not an OS security boundary.
5. Remote branch protection and required CODEOWNER review must be verified/configured by the repository administrator.
