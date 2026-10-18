# Alpha Lab Research Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one candidate CLI command run a reproducible baseline comparison, archive immutable inputs, index honest research summaries in the existing SQLite database, and safely reuse identical completed research.

**Architecture:** Keep `run_experiment()` as the only execution path and `ExperimentRegistry` as the sole owner of `experiments`. Add focused snapshot/identity helpers plus `ResearchStore` as a recoverable SQLite index over canonical run artifacts; CLI commands compose those existing boundaries rather than creating a second evaluator.

**Tech Stack:** Python 3, argparse, dataclasses, pathlib, sqlite3, pandas, scikit-learn, pytest.

**Spec:** `docs/work.md`

## Global Constraints

- Do not change IC, RankIC, label, Walk-Forward/purge, LogisticRegression, or Bootstrap mathematical definitions to improve scores.
- Do not read or touch 2025 or 2026 market data; accepted research end remains exclusive `2025-01-01`.
- Do not delete or overwrite old reports, candidate source, or historical database rows.
- Add/migrate only `candidates` and `evaluations`; `ExperimentRegistry` exclusively owns `experiments`.
- SQLite stores compact query fields; prediction series, detailed monthly/Bootstrap reports, and CSVs remain files.
- Missing/non-applicable metrics are SQL NULL, never fabricated zeroes.
- Preserve existing user changes and backward compatibility for non-candidate `run_experiment(config_path)`.

## Review Focus

- A completed database row with a missing/corrupt report or snapshot must never be reused; `test_research_key_reuse_force` covers it.
- An unexpired concurrent `running` claim must not start a duplicate, while stale/failed attempts remain recoverable; `test_run_lifecycle_recovery` covers it.
- A summary without an `experiments` parent must be reported and skipped; `test_backfill_missing_parent` covers it.
- A repeated index call with a conflicting candidate identity must fail rather than rebind the run; `test_research_store_idempotency` covers it.
- Explicit no-baseline mode must use NULL incremental fields and an honest status, not zeroes; `test_explicit_no_baseline` covers it.

---

### Task 1: Candidate CLI Baseline Contract

**Files:**
- Modify: `alpha_lab/candidate_cli.py`
- Modify: `alpha_lab/config.py`
- Create: `alpha_lab/tests/test_candidate_cli.py`

**Interfaces:**
- Consumes: existing `ExperimentConfig.from_json()` factor schema and `run_experiment(config_path, candidate_dir=...)`.
- Produces: `default_baseline_factors() -> tuple[FactorSpec, ...]`; CLI flags `--baseline-config`, `--no-baseline`, `--force`; machine-readable result containing `run_id`, `reused`, evaluation mode, and nullable metrics.

- [ ] **Step 1: Write failing CLI/config tests** for default mom4+mom12, explicit no-baseline, baseline-config preservation, mutual exclusion, and one `run_experiment` call.
- [ ] **Step 2: Run** `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/test_candidate_cli.py alpha_lab/tests/test_config.py -q`; expect new tests to fail.
- [ ] **Step 3: Implement the baseline configuration contract** using the existing JSON schema and pass `force` through without adding a second evaluation path.
- [ ] **Step 4: Re-run the focused tests**; expect PASS.

### Task 2: Immutable Candidate Snapshot and Manifest

**Files:**
- Modify: `alpha_lab/factors/candidate.py`
- Modify: `alpha_lab/runner.py`
- Modify: `alpha_lab/tests/test_candidate.py`
- Create: `alpha_lab/tests/test_runner_snapshot.py`

**Interfaces:**
- Consumes: validated `CandidateSpec` and allocated `run_id`.
- Produces: `snapshot_candidate(candidate_dir, run_dir) -> CandidateSnapshot` with `candidate_dir`, relative paths, manifest hash, source hash; `_execute_experiment` loads only the snapshot directory.

- [ ] **Step 1: Write failing tests** `test_candidate_snapshot_immutable` and `test_snapshot_hash_matches_loaded_code`, including the required `candidate_snapshot/<candidate.name>/` layout.
- [ ] **Step 2: Run focused tests** and confirm failures identify missing snapshot behavior.
- [ ] **Step 3: Implement atomic copy-and-verify helpers**, rejecting source changes between pre/post-copy hashes and preserving strict directory-name validation.
- [ ] **Step 4: Change Runner ordering** to allocate the run directory/snapshot before candidate import and write an atomic `manifest.json` containing original/snapshot paths and hashes.
- [ ] **Step 5: Re-run focused tests**; expect PASS.

### Task 3: ResearchStore Schema, Migration, and Honest Summary Indexing

**Files:**
- Create: `alpha_lab/research_store.py`
- Modify: `alpha_lab/experiment_registry.py`
- Create: `alpha_lab/tests/test_research_store.py`

**Interfaces:**
- Consumes: the existing `experiments.sqlite3`, a completed parent run, and canonical `summary.json`/`manifest.json`.
- Produces: `ResearchStore.index_summary(run_id, summary_path)`, `list_runs(...)`, `show_run(run_id)` and idempotent schema upgrades with candidate/run identity checks.

- [ ] **Step 1: Write failing tests** for fresh schema, upgrade from old tables, FK enforcement, repeated indexing, conflicting candidate IDs, NULL incremental metrics, signed IC/RankIC, and query filters.
- [ ] **Step 2: Run focused tests** and confirm failure.
- [ ] **Step 3: Implement additive schema migration** for the exact `candidates`/`evaluations` fields in `docs/work.md`, necessary indexes, `PRAGMA foreign_keys=ON`, and stable legacy candidate fingerprint compatibility.
- [ ] **Step 4: Implement summary parsing/upsert** with explicit `candidate_auc`, `baseline_auc`, `combined_auc`, mode/status, monthly counts, compact feedback, and no source body/time series.
- [ ] **Step 5: Re-run focused tests**; expect PASS.

### Task 4: Runner Lifecycle, Research Identity, Reuse, and Recovery

**Files:**
- Modify: `alpha_lab/runner.py`
- Modify: `alpha_lab/experiment_registry.py`
- Modify: `alpha_lab/tests/test_runner_lifecycle.py`
- Create: `alpha_lab/tests/test_runner_research_key.py`

**Interfaces:**
- Consumes: canonical candidate fingerprint, ordered baseline specs, data fingerprint, label horizon/window, model config, Bootstrap config, and evaluation code version.
- Produces: strict `research_key` (stored compatibly in `config_hash` and manifest), `run_experiment(..., force=False)` returning `reused`, and a recoverable complete-then-index lifecycle.

- [ ] **Step 1: Write failing tests** for same-key reuse, force creating a new run, changed source/baseline/horizon/data fingerprint not reusing, failed/corrupt run not reusing, and running-claim behavior.
- [ ] **Step 2: Write failing lifecycle test** where artifact write succeeds but indexing fails; assert the command raises and backfill can recover rather than silently claiming indexed success.
- [ ] **Step 3: Run focused tests** and confirm failures.
- [ ] **Step 4: Implement canonical research identity and artifact validation** without altering old config parsing or mathematical evaluation.
- [ ] **Step 5: Implement deterministic claim/reuse policy** through `ExperimentRegistry`, with stale running records not permanently blocking and `--force` bypassing reuse.
- [ ] **Step 6: Wire `ResearchStore.index_summary` after atomic artifacts and completed state**; propagate index failure with a repair message.
- [ ] **Step 7: Re-run focused tests**; expect PASS.

### Task 5: Three Evaluation Branches and Research Metadata

**Files:**
- Modify: `alpha_lab/runner.py`
- Modify: `alpha_lab/evaluation/incremental.py`
- Modify: `alpha_lab/evaluation/feedback.py`
- Modify: `alpha_lab/tests/test_incremental.py`
- Modify: `alpha_lab/tests/test_feedback.py`
- Create: `alpha_lab/tests/test_runner_modes.py`

**Interfaces:**
- Consumes: config factors, optional candidate, `evaluate_incremental(..., return_model_results=True)`.
- Produces: builtin-only/candidate-only one-model outputs; candidate+baseline exactly three model outputs; explicit baseline metadata, sample alignment metadata, evaluation mode/status, and cautious feedback limitations.

- [ ] **Step 1: Write failing tests** for `test_three_model_no_duplicate_train`, `test_common_sample_alignment`, signed factor metrics, baseline source/feature names, and explicit single-factor null incremental fields.
- [ ] **Step 2: Run focused tests** and confirm failures.
- [ ] **Step 3: Complete the three-branch report contract**, preserving the existing three-model reuse and legacy artifact names.
- [ ] **Step 4: Add sample eligibility/timestamp checks and metadata** without changing purge or model math.
- [ ] **Step 5: Extend feedback language** to state that CLV is a price-location proxy, mechanism claims are untested, correlation is only against current baselines, Bootstrap ratios are not truth probabilities, and 2023–24 is development/rolling validation.
- [ ] **Step 6: Re-run focused tests**; expect PASS.

### Task 6: SQLite Backfill and Query CLI

**Files:**
- Create: `alpha_lab/research_cli.py`
- Remove from formal path: `alpha_lab/research_history.py` (retain only if needed as a compatibility wrapper)
- Create: `alpha_lab/tests/test_research_cli.py`
- Create: `alpha_lab/tests/test_research_backfill.py`

**Interfaces:**
- Consumes: `ResearchStore`, `runs/*/summary.json`, and existing experiment parent rows.
- Produces: `list`, `show`, and `backfill [--dry-run]` JSON/text contracts with candidate/status/limit filters and categorized counts (`indexed`, `already_indexed`, `missing_parent`, `invalid`, `conflict`).

- [ ] **Step 1: Write failing backfill tests** for old single-candidate, incremental, missing-parent, malformed summary, dry-run, and two-pass idempotency.
- [ ] **Step 2: Write failing CLI contract tests** for parseable `list/show --json`, failed history visibility, filters, compact output, and report paths.
- [ ] **Step 3: Run focused tests** and confirm failures.
- [ ] **Step 4: Implement parent-authoritative backfill** using only standard run summaries; do not infer links from candidate names or standalone incremental reports.
- [ ] **Step 5: Implement query CLI** over SQLite, keeping detailed monthly/Bootstrap data behind `report_path`.
- [ ] **Step 6: Re-run focused tests**; expect PASS.

### Task 7: Documentation and Full Verification

**Files:**
- Modify: relevant Alpha Lab README/help documentation discovered in the repository
- Modify: `task_plan.md`, `findings.md`, `progress.md`

**Interfaces:**
- Consumes: all prior task contracts.
- Produces: documented single-command workflow, migration/repair/reuse semantics, and a truthful acceptance record.

- [ ] **Step 1: Back up the real SQLite file** to a timestamped sibling and record its checksum/counts before migration.
- [ ] **Step 2: Run static/full tests:** `python -m compileall -q alpha_lab` and `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/ -q`; record exact output.
- [ ] **Step 3: Run real backfill twice** (dry-run, apply, apply again), query the named historical runs, and verify counts stabilize.
- [ ] **Step 4: Run one real forced candidate research command** for `intrabar_pressure_reversal`; verify exactly one run ID, all required artifacts, snapshot hashes, Bootstrap groups, feedback, and SQLite row.
- [ ] **Step 5: Run reuse and immutable-source acceptance checks** in safe copied/temp candidate paths; do not modify the canonical candidate.
- [ ] **Step 6: Audit every Gate 1–5 requirement** against files, DB queries, and command logs; document any genuine residual issue instead of claiming success.
