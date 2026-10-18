# Evidence Report and Research Decision Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Alpha Lab persist objective, provenance-linked Evidence Reports while Research Agent decisions are validated, append-only records stored separately from experimental evaluation.

**Architecture:** Replace the policy-bearing feedback builder with a pure Evidence Report assembler that composes existing evaluator outputs without changing their mathematics. Keep canonical evidence in each run's immutable `summary.json`, index objective fields in `evaluations`, and add a separate append-only `research_decisions` table/API keyed by Run ID and exact candidate source hash.

**Tech Stack:** Python 3, pathlib, JSON, sqlite3, pandas, scikit-learn, pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-evidence-report-research-decision-separation-design.md`

## Global Constraints

- Do not change factor formulas, label construction, model choice, metric definitions, Walk-Forward splits/purge, sample alignment, or Bootstrap calculations.
- Alpha Lab must not emit research classifications, verdict tags, economic interpretations, mechanism claims, or recommended actions.
- New Evidence Reports must reference their Run ID and exact candidate source hash.
- Research Decisions must be stored separately and append-only; writing one must never rewrite `summary.json`, detailed CSV artifacts, or an earlier decision.
- A Research Decision must validate its Run ID, completed/indexed experiment, candidate identity, and source hash in one transaction.
- Keep legacy `research_feedback` fields and database columns readable, but never promote them into Research Decisions.
- Preserve all unrelated and pre-existing uncommitted workspace changes.

## Review Focus

- A candidate name, including `intrabar_pressure_reversal`, must not select different Evidence Report prose or policy; Task 1 pins name-independence.
- Missing or non-applicable incremental evaluation must be represented explicitly rather than fabricated as zero; Tasks 1 and 2 pin both branches.
- A decision referencing a valid Run ID but stale source hash must be rejected atomically; Task 4 pins the identity mismatch.
- Re-indexing or backfilling a run with decisions must not delete or mutate them; Tasks 3 and 5 pin append-only isolation.
- Legacy summaries containing policy feedback must remain queryable without becoming new decisions; Tasks 3 and 5 pin compatibility.

---

### Task 1: Pure Evidence Report Builder

**Files:**
- Create: `alpha_lab/evaluation/evidence.py`
- Modify: `alpha_lab/tests/test_feedback.py`

**Interfaces:**
- Consumes: `build_evidence_report(*, run_id: str, candidate: dict | None, factor_evaluation: dict, model_evaluation: dict, incremental_evaluation: dict | None, evaluation_mode: str, incremental_status: str, baseline: dict | None, data: dict, model: dict, bootstrap: dict) -> dict`.
- Produces: a JSON-safe, policy-free report with `schema_version`, `run_id`, `candidate`, `evaluation_context`, `factor_evaluation`, `model_evaluation`, and nullable `incremental_evaluation`.

- [ ] **Step 1: Replace policy tests with failing evidence-contract tests** asserting preservation of quality/IC/RankIC, prediction-model metrics, similarity, incremental metrics, stability/Bootstrap, data/OOS metadata, Run ID, and source hash; assert forbidden keys (`classification`, `tags`, `suggested_action`, `policy_version`, `interpretation`) and policy phrases are absent recursively.
- [ ] **Step 2: Add a failing candidate-name independence test** that builds reports for identical inputs under `intrabar_pressure_reversal` and another name and proves only candidate identity fields differ.
- [ ] **Step 3: Run** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_feedback.py -q`; expect failures because `build_evidence_report` does not exist and current feedback contains policy.
- [ ] **Step 4: Implement `build_evidence_report`** as structural composition/copying only; validate required Run ID and candidate source hash, carry explicit applicability status, and add no thresholds or inferred narratives.
- [ ] **Step 5: Re-run the focused evidence tests**; expect PASS while the old feedback module remains temporarily available to existing imports until Task 2.

### Task 2: Runner Emits Canonical Evidence Without Touching Evaluators

**Files:**
- Modify: `alpha_lab/runner.py`
- Remove: `alpha_lab/evaluation/feedback.py`
- Modify: `alpha_lab/tests/test_runner_modes.py`
- Modify: `alpha_lab/tests/test_runner_snapshot.py`
- Modify: `alpha_lab/tests/test_runner_research_key.py`

**Interfaces:**
- Consumes: existing outputs from `evaluate_factor`, `evaluate_model`, and `evaluate_incremental` plus run/config/manifest metadata.
- Produces: new-run `summary.json` with `evidence_report` and without `research_feedback`; unchanged detailed factor/prediction/fold artifacts.

- [ ] **Step 1: Write failing runner tests** asserting incremental and single-factor summaries use `evidence_report`, contain Run ID/source hash/data/model/baseline/Bootstrap context, and omit `research_feedback`.
- [ ] **Step 2: Extend the existing three-model test** to assert exactly one `evaluate_incremental` call, no duplicate `evaluate_model` call, and byte-for-structure reuse of the existing incremental output inside evidence.
- [ ] **Step 3: Run** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_runner_modes.py alpha_lab/tests/test_runner_snapshot.py alpha_lab/tests/test_runner_research_key.py -q`; expect evidence contract failures.
- [ ] **Step 4: Replace the feedback import/call with `build_evidence_report`** after all existing evaluation outputs and metadata are available; do not edit evaluator algorithms.
- [ ] **Step 5: Keep `EVALUATION_CODE_PATHS` accurate** by replacing the removed feedback path with the evidence module, preserving research-key invalidation when report code changes.
- [ ] **Step 6: Delete the obsolete feedback module** after its production and test imports have moved.
- [ ] **Step 7: Re-run Task 1 and Task 2 focused tests**; expect PASS.

### Task 3: Objective Evidence Index and Legacy Compatibility

**Files:**
- Modify: `alpha_lab/research_store.py`
- Modify: `alpha_lab/tests/test_research_store.py`

**Interfaces:**
- Consumes: canonical new `evidence_report`, or legacy `research_feedback` only for backward-compatible reads.
- Produces: additive `evaluations.evidence_json`; new rows leave `classification`, `tags_json`, and `feedback_json` null while legacy rows retain their historical values.

- [ ] **Step 1: Write failing migration/index tests** for adding `evidence_json` to an old database, indexing objective evidence, leaving policy columns null on new runs, and preserving legacy feedback columns for old summaries.
- [ ] **Step 2: Write a failing idempotency test** proving re-indexing refreshes objective evidence without creating any decision rows.
- [ ] **Step 3: Run** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_research_store.py -q`; expect schema/index failures.
- [ ] **Step 4: Add the objective evidence column and dual-format parser**; extract compact metrics from evaluator sections exactly as today and serialize the full Evidence Report without inference.
- [ ] **Step 5: Restrict legacy feedback parsing to summaries that lack `evidence_report`**; do not synthesize evidence classifications or decisions.
- [ ] **Step 6: Re-run focused tests**; expect PASS.

### Task 4: Append-Only Research Decision Store

**Files:**
- Modify: `alpha_lab/research_store.py`
- Modify: `alpha_lab/tests/test_research_store.py`

**Interfaces:**
- Produces: `ResearchStore.create_decision(*, run_id: str, candidate_source_hash: str, supporting_evidence: list | dict, opposing_evidence: list | dict, conclusion: str | dict, next_hypothesis: str | dict, next_actions: list | dict, agent_identity: str | None = None) -> dict`.
- Produces: `ResearchStore.list_decisions(run_id: str) -> list[dict]` ordered by creation time and decision ID.
- Storage: `research_decisions(decision_id, run_id, candidate_id, candidate_source_hash, supporting_evidence_json, opposing_evidence_json, conclusion_json, next_hypothesis_json, next_actions_json, agent_identity, created_at)` with foreign keys and no public update/delete path.

- [ ] **Step 1: Write failing schema/API tests** for successful creation, JSON round-trip, multiple decisions on one run, deterministic ordering, and absence of automatic decisions after indexing.
- [ ] **Step 2: Write failing integrity tests** for missing/incomplete/unindexed Run ID, wrong source hash, candidate mismatch, non-serializable/non-finite payloads, and an unchanged `summary.json` before/after decision creation.
- [ ] **Step 3: Add a failing isolation test** proving re-indexing a run after two decisions neither mutates nor deletes either decision.
- [ ] **Step 4: Run the focused ResearchStore tests** and confirm failures identify the missing table/API.
- [ ] **Step 5: Add the table/indexes via additive initialization** and implement UUID decision IDs plus strict JSON normalization using the existing canonical serializer.
- [ ] **Step 6: Implement transactional identity validation and INSERT-only creation**; expose no overwrite, upsert, update, or delete operation.
- [ ] **Step 7: Re-run all ResearchStore tests**; expect PASS, including re-index isolation.

### Task 5: Research Decision CLI and Backfill Isolation

**Files:**
- Modify: `alpha_lab/research_cli.py`
- Modify: `alpha_lab/tests/test_research_cli.py`
- Modify: `alpha_lab/tests/test_research_backfill.py`
- Modify: `alpha_lab/research_history.py`
- Modify: `alpha_lab/tests/test_research_history.py`

**Interfaces:**
- Produces: CLI `decide --run-id ... --source-hash ... --decision-file ...` calling `create_decision` and `decisions --run-id ... [--json]` calling `list_decisions`.
- Decision file requires `supporting_evidence`, `opposing_evidence`, `conclusion`, `next_hypothesis`, and `next_actions`; optional `agent_identity`.
- Compatibility history exposes `evidence_report` and legacy feedback as distinct fields; decisions remain available only through `ResearchStore`/the decision CLI rather than being inferred from filesystem summaries.

- [ ] **Step 1: Write failing CLI tests** for creating/listing two decisions, malformed/missing decision fields, source-hash mismatch, and parseable JSON output.
- [ ] **Step 2: Update history tests first** to require separate `evidence_report` and `legacy_research_feedback` concepts and no synthesized `research_decisions` field; ensure no historical feedback is labeled as a decision.
- [ ] **Step 3: Add a failing backfill test** proving backfill indexes evidence but neither creates nor deletes decisions on repeated runs.
- [ ] **Step 4: Run** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_research_cli.py alpha_lab/tests/test_research_backfill.py alpha_lab/tests/test_research_history.py -q`; expect contract failures.
- [ ] **Step 5: Implement CLI parsing/validation and separate history fields** using `ResearchStore`; keep experiment execution entirely outside the decision command.
- [ ] **Step 6: Re-run focused tests**; expect PASS.

### Task 6: Documentation and Regression Verification

**Files:**
- Modify: `alpha_lab/README.md`
- Modify: `docs/work.md`

**Interfaces:**
- Documents: Evidence Report schema/responsibility, append-only Research Decision input, Run ID/source-hash linkage, legacy behavior, and unchanged evaluator semantics.

- [ ] **Step 1: Update documentation** to remove statements that `build_research_feedback()` classifies or recommends research and show the separate evidence/decision workflow.
- [ ] **Step 2: Run static verification:** `python -m compileall -q alpha_lab`; expect exit 0.
- [ ] **Step 3: Run the focused suite:** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_feedback.py alpha_lab/tests/test_runner_modes.py alpha_lab/tests/test_runner_snapshot.py alpha_lab/tests/test_runner_research_key.py alpha_lab/tests/test_research_store.py alpha_lab/tests/test_research_cli.py alpha_lab/tests/test_research_backfill.py alpha_lab/tests/test_research_history.py -q`; expect all PASS.
- [ ] **Step 4: Run the full Alpha Lab suite:** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/ -q`; report every failure by name, including pre-existing failures.
- [ ] **Step 5: Search for prohibited new-policy output:** `rg -n "build_research_feedback|suggested_action|policy_version|intrabar_pressure_reversal|HIGH_REDUNDANCY|WEAK_INCREMENTAL_EVIDENCE|NO_CONSISTENT_POINT_IMPROVEMENT" alpha_lab`; remaining matches must be explicitly legacy fixtures/read paths or removed.
- [ ] **Step 6: Inspect the final diff** to confirm no mathematical evaluator, Walk-Forward, or Bootstrap implementation changed beyond imports/report plumbing.
