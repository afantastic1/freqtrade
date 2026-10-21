# Alpha Lab Unified Descriptive Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver mode-correct descriptive evaluation, provenance-bound immutable Evidence, and a unified read-only history query for Alpha Lab.

**Architecture:** Add a versioned descriptive evaluation module with TS/CS dispatch over the trusted v2 run. Publish a canonical Evidence payload only after complete execution, and add a read-only history facade over legacy, v1, and v2 Stores without changing their schemas.

**Tech Stack:** Python 3, pandas, NumPy, SQLite, pytest, existing Alpha Lab ResearchRequest/CampaignPolicy and Factor Service v2.

**Spec:** `docs/superpowers/specs/2026-10-21-alpha-lab-unified-evidence-design.md`

## Global Constraints

- Existing v1 service, legacy evaluators, schemas, and Store layouts remain unchanged.
- Evidence and Decision are append-only; replay of identical identity and content is idempotent; identity conflicts are rejected.
- Formal inference remains `BLOCKED`; do not publish p-values, confidence intervals, or calibrated inferential claims.
- `NOT_EVALUABLE` is a terminal descriptive result with a stable reason, not a favorable fallback.
- Worker inputs exclude evaluator labels, and Evidence is published only after every planned chunk commits.
- Cost-adjusted returns are explicitly assumption-based diagnostics and are not executable strategy PnL.
- Do not migrate historical data, modify raw data, or alter `alpha_lab/freeze-manifest-v1.1.json` bytes.

## Review Focus

- Incomplete or duplicated Worker schedules must not create Evidence; pin this in the service integration task.
- Single-symbol time-series reports must not be sent through a cross-sectional evaluator; pin in evaluator and service tests.
- Very sparse/constant series or cross-sections must return `NOT_EVALUABLE` with a stable reason; pin in evaluator tests.
- A missing/unreadable configured history source must be reported as partial, never silently omitted; pin in history tests.
- Cost assumptions and nonfinite input values must be explicit and must not leak inferential outputs; pin in shared report tests.

---

### Task 1: Mode-specific descriptive evaluators

**Files:**
- Create: `alpha_lab_service/evaluation_v2.py`
- Modify: `alpha_lab_service/tests/test_evaluator_v2.py`
- Modify: `alpha_lab_service/tests/test_evaluator.py` only if shared compatibility assertions are needed

**Interfaces:**
- Consumes: authorized `run` with `research_request.mode`, `evaluation_timestamps`, dataset labels, and ordered factor `pd.Series` on `(open_time, symbol)`.
- Produces: `evaluate_authorized_factor_output(outputs: pd.Series, run, *, identity: str) -> dict`; report contains `identity`, `mode`, `status`, stable `reason`, descriptive `metrics`, and `formal_inference_status: "BLOCKED"`.

- [x] **Step 1: Write failing TS and CS dispatch tests**

Add `test_time_series_evaluator_reports_ic_rank_ic_monthly_yearly_and_coverage` asserting TS mode, correct IC/RankIC direction, month/year buckets, valid/missing counts, and no CS-only metrics. Add `test_cross_sectional_evaluator_reports_quantiles_asset_counts_and_stability` asserting CS per-time rank IC, quantile returns, asset counts, and stability.

- [x] **Step 2: Run focused tests and verify expected failure**

Run: `python -m pytest alpha_lab_service/tests/test_evaluator_v2.py -q`
Expected: FAIL because mode-aware `evaluation_v2` module/dispatcher is absent.

- [x] **Step 3: Implement mode-specific descriptive reports**

Implement TS evaluation over the single authorized symbol with descriptive IC/RankIC, grouped forward-return summaries and monthly/yearly stability. Implement CS evaluation with minimum valid assets, per-time IC/RankIC, quantile returns, counts, and stability. Filter nonfinite factor/label pairs for metric calculations while retaining requested/valid/missing coverage counts. Do not expose fields returned by the legacy CS evaluator that are inferential.

- [x] **Step 4: Add failure/diagnostic tests and run focused suite**

Add `test_sparse_or_constant_inputs_return_stable_not_evaluable_reason` for both modes, `test_cost_diagnostic_records_assumptions_and_turnover`, and `test_evaluators_never_emit_inference_fields`. Assert stable reason codes and absence of p-value, CI, bootstrap, HAC, or MDE fields.

Run: `python -m pytest alpha_lab_service/tests/test_evaluator_v2.py alpha_lab_service/tests/test_evaluator.py -q`
Expected: PASS; legacy evaluator tests remain unchanged and green.

- [x] **Step 5: Commit evaluator contract**

```bash
git add alpha_lab_service/evaluation_v2.py alpha_lab_service/tests/test_evaluator_v2.py
git commit -m "feat(alpha-lab): add mode-aware descriptive evaluators"
```

### Task 2: Provenance-bound Evidence publication

**Files:**
- Modify: `alpha_lab_service/jobs_v2.py`
- Modify: `alpha_lab_service/evaluator.py` (compatibility re-export only, if needed)
- Modify: `alpha_lab_service/store_v2.py` only for validation defects proven by tests
- Test: `alpha_lab_service/tests/test_evaluator_v2.py`
- Test: `alpha_lab_service/tests/test_v2_recovery.py`
- Test: `alpha_lab_service/tests/test_store_v2.py`

**Interfaces:**
- Consumes: Task 1 `evaluate_authorized_factor_output`; current `ResearchServiceV2` trusted resolved run and `ResearchStoreV2.complete_with_evidence` transaction.
- Produces: a canonical Evidence record containing Campaign/policy, request digest, Dataset manifest digest/fingerprint/source pins, mode, source SHA-256, canonical params, worker image digest, evaluator version, complete output/chunk hashes, report, and inference block status.

- [x] **Step 1: Write failing evidence identity and provenance tests**

Add `test_v2_evidence_binds_campaign_dataset_source_params_and_evaluator` asserting all specified provenance keys exist and identity deterministically binds them. Add `test_evidence_replay_is_idempotent_and_conflict_is_rejected` for same run identity with same versus changed payload.

- [x] **Step 2: Run focused tests and verify expected failure**

Run: `python -m pytest alpha_lab_service/tests/test_evaluator_v2.py::test_v2_evidence_binds_campaign_dataset_source_params_and_evaluator alpha_lab_service/tests/test_store_v2.py -q`
Expected: FAIL because the service report is not mode-aware and omits full Campaign/source provenance.

- [x] **Step 3: Build canonical Evidence from trusted resolved inputs**

Route v2 evaluation through Task 1. Populate all provenance only from `run`, trusted request and chunk plan; do not accept caller-supplied provenance. Keep factor source text out of public report/history and store its digest. Include exact output hashes and `formal_inference_status: "BLOCKED"`. Keep one atomic complete-only Store publication.

- [x] **Step 4: Test incomplete execution and deterministic replay**

Add `test_incomplete_v2_task_cannot_publish_evidence` and an integration replay assertion that repeated identical submission returns the same Evidence hash while modified identity inputs produce a different run identity or a hard conflict. Run:

`python -m pytest alpha_lab_service/tests/test_evaluator_v2.py alpha_lab_service/tests/test_store_v2.py alpha_lab_service/tests/test_v2_recovery.py -q`

Expected: PASS; no Evidence row exists until all planned chunks commit and evaluation succeeds.

- [x] **Step 5: Commit provenance-bound Evidence**

```bash
git add alpha_lab_service/jobs_v2.py alpha_lab_service/evaluator.py alpha_lab_service/store_v2.py alpha_lab_service/tests/test_evaluator_v2.py alpha_lab_service/tests/test_v2_recovery.py alpha_lab_service/tests/test_store_v2.py
git commit -m "feat(alpha-lab): bind descriptive evidence provenance"
```

### Task 3: Unified read-only history facade

**Files:**
- Create: `alpha_lab_service/history_v2.py`
- Modify: `alpha_lab_service/store.py` only if a read-only projection is missing
- Modify: `alpha_lab_service/store_v2.py` only if a read-only projection is missing
- Test: `alpha_lab_service/tests/test_history_v2.py`

**Interfaces:**
- Consumes: injected legacy `ResearchStore`, optional `ResearchStoreV1`, and optional `ResearchStoreV2` instances; their existing read-only listing APIs.
- Produces: `UnifiedResearchHistory(stores: Mapping[str, object]).list_history(limit: int = 100) -> dict` with normalized `entries`, `source_status`, and per-entry store/protocol provenance. A failed configured source is explicitly `UNAVAILABLE`; missing optional source is `NOT_CONFIGURED`.

- [x] **Step 1: Write failing facade tests against real temporary Stores**

Add `test_unified_history_merges_sources_with_store_provenance`, `test_history_reports_unavailable_configured_source_without_silent_drop`, `test_history_limit_is_bounded`, and `test_history_query_does_not_mutate_store_bytes`.

- [x] **Step 2: Run tests and verify missing facade failure**

Run: `python -m pytest alpha_lab_service/tests/test_history_v2.py -q`
Expected: FAIL because `history_v2` is absent.

- [x] **Step 3: Implement bounded read-only adapters and merge**

Normalize each Store's existing summaries without opening write connections; tag every entry with `store_id` and protocol/version. Sort deterministically by creation timestamp and identity, cap `limit` to 1–100, and return explicit per-source status for errors. Do not silently suppress configured-source failures.

- [x] **Step 4: Verify facade and historical immutability**

Run: `python -m pytest alpha_lab_service/tests/test_history_v2.py alpha_lab_service/tests/test_store.py alpha_lab_service/tests/test_store_v2.py -q`
Expected: PASS; database files, table schemas, and row counts are unchanged by history reads.

- [x] **Step 5: Commit unified history facade**

```bash
git add alpha_lab_service/history_v2.py alpha_lab_service/store.py alpha_lab_service/store_v2.py alpha_lab_service/tests/test_history_v2.py
git commit -m "feat(alpha-lab): add unified read-only research history"
```

### Task 4: Service integration, manifests, and acceptance

**Files:**
- Modify: `alpha_lab_service/jobs_v2.py`
- Modify: `alpha_lab_service/server.py`
- Modify: `alpha_lab_service/runtime.py`
- Modify: `alpha_lab_service/manifest.py`
- Modify: `alpha_lab_service/protocol-manifest-v3.json`
- Modify: `alpha_lab/freeze-manifest-v1.4.json`
- Modify: `alpha_lab_service/tests/test_mcp_v2_tools.py`
- Modify: `alpha_lab_service/tests/test_ci_protection.py`
- Modify: `.github/CODEOWNERS` only if the new files are outside the existing service owner boundary
- Create: `alpha_lab_service/tests/test_history_v2.py` (from Task 3)
- Create: `docs/alpha_lab/unified-descriptive-evidence-acceptance.md`
- Create: `docs/alpha_lab/unified-descriptive-evidence-acceptance.json`

**Interfaces:**
- Consumes: Task 1 evaluator, Task 2 Evidence payload and Task 3 facade.
- Produces: additive MCP `get_history_v2(limit: int = 100) -> dict`, and real/synthetic acceptance evidence for TS and CS with exact HEAD, dataset pins, test commands, and independent data/research/security statuses.

- [x] **Step 1: Write failing MCP exposure and real-data route tests**

Add `test_mcp_v2_exposes_unified_history_read_only` asserting tool output contains v1/v2/legacy source status and no mutation capability. Add an end-to-end TS v2 run assertion for `BTCUSDT` 5m May 2025 and a synthetic authorized multi-symbol CS run; both must return mode-specific descriptive report and `BLOCKED` inference.

- [x] **Step 2: Run tests and verify absent unified tool/mode routing**

Run: `python -m pytest alpha_lab_service/tests/test_mcp_v2_tools.py -q`
Expected: FAIL because `get_history_v2` is not registered and TS v2 routing is absent.

- [x] **Step 3: Wire trusted history facade into runtime and MCP**

Inject Store adapters only from trusted runtime composition. Register the additive read-only MCP method when the v2 service is present. Ensure legacy MCP tool names and behavior remain unchanged.

- [x] **Step 4: Update active manifests and governance path coverage**

Extend `SERVICE_V3_FILES` in `alpha_lab_service/manifest.py` for the Evidence acceptance records if needed, regenerate `alpha_lab_service/protocol-manifest-v3.json` and `alpha_lab/freeze-manifest-v1.4.json`, and ensure `alpha_lab_service/tests/test_ci_protection.py` protects the new service paths. Add a CODEOWNERS rule only if a new file is not already covered by `/alpha_lab_service/`. Do not edit `alpha_lab/freeze-manifest-v1.1.json`.

- [x] **Step 5: Run focused and full relevant validation**

Run:

```bash
python -m pytest alpha_lab_service/tests/test_evaluator_v2.py alpha_lab_service/tests/test_history_v2.py alpha_lab_service/tests/test_mcp_v2_tools.py -q
ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced python -m pytest alpha_lab_service/tests -q
python -m pytest alpha_lab_contracts/tests -q
python -m alpha_lab_service.governance
git diff --check
```

Expected: all tests and governance pass; real and synthetic data evidence are reported separately; v1.1 manifest hash matches the recorded original value; inference remains blocked.

- [x] **Step 6: Write acceptance records and commit integration**

Acceptance documents record exact commit HEAD, real Binance TS snapshot digests, synthetic CS fixture identity, test commands/results, independent layer status, and remaining deployment/branch-protection gates. Commit:

```bash
git add alpha_lab_service docs/alpha_lab/unified-descriptive-evidence-acceptance.md docs/alpha_lab/unified-descriptive-evidence-acceptance.json
git commit -m "feat(alpha-lab): integrate unified descriptive evidence"
```

