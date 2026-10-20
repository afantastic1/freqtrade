# Alpha Lab v1.1 Admission Matrix

Overall status: **`RC_BLOCKED`**. This matrix is fail-closed. The only permitted transition to `ADMITTED` is when every required row is `PASS` for the exact requested scope and evidence hashes.

| Required gate | Machine acceptance condition | Current result | Stop / unblock condition |
|---|---|---|---|
| Core Manifest valid | `python -m alpha_lab.freeze` exits 0 against v1.1 manifest | **PASS** | Any hash/version/missing-file error blocks. |
| Data Panel reproducible | Manifest schema/hash/source/builder hashes match; fixed symbols/timeframe/date boundaries; no out-of-window availability; complete quality validation; independent builds byte-identical | **PASS** | Source drift, panel drift, missing/extra symbols, timestamp breach, or failed quality check blocks. |
| Selected inference method qualified | Method in immutable registry has `status=PASS`, `role=inference`, and qualification JSON says `QUALIFIED` for the exact scope | **BLOCKED** (`cs-time-block-centered-v1`) | Existing MBB/HAC calibration failed. A future new method/version needs a separately reviewed qualification; do not weaken thresholds or overwrite history. |
| Agent isolation verified | Structured attestation says the actual OpenCode/Codex process runs inside OS worker and every required negative test passes; doc changes remain subject to CODEOWNER review | **BLOCKED** | Docker probe passed only for an isolated test process; actual OpenCode loaded no agent; Codex runtime not introspectable. |
| Evidence/Decision store integrity | Read-only integrity/FK checks pass, v1.1 CS tables exist, append-only UPDATE/DELETE triggers verified | **BLOCKED** | Legacy DB is integral but has no CS evidence/decision tables. Validate a separate versioned store; do not migrate or write historical DB here. |

## Machine gate

The verifier is [scripts/alpha_lab_admission_gate.py](../../scripts/alpha_lab_admission_gate.py), outside `alpha_lab/` and outside the frozen manifest. It checks the frozen manifest directly, verifies panel source and artifact hashes/content, reads the qualification and isolation attestations, and opens the Research Store in immutable read-only SQLite mode. It writes only the requested verdict file; it never creates research runs or writes the database.

Current intended scope is `cross-sectional exploratory research, five-asset 1h 2022-2024`, with `cs-time-block-centered-v1` selected as inference method. Although descriptive Pearson/Spearman point estimates are qualified for diagnostic display, that does not satisfy the intended inferential scope.

Example:

```text
.venv/bin/python scripts/alpha_lab_admission_gate.py gate \
  --panel-manifest user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv.manifest.json \
  --qualification docs/alpha_lab/inference_qualification.json \
  --isolation docs/alpha_lab/agent_isolation_attestation.json \
  --method-id cs-time-block-centered-v1 \
  --output docs/alpha_lab/admission_verdict.json
```

Exit code 0 means all gates are `PASS` and the scope is `ADMITTED`. Exit code 1 means any required gate is unresolved and verdict stays `RC_BLOCKED`. A passing CI test suite or valid core manifest alone never grants admission.
