# Evidence Report and Research Decision Separation

## Purpose

Alpha Lab reports what an experiment observed. It does not decide whether a
candidate is promising, assign a research category, explain the candidate's
economic mechanism, or prescribe the next experiment. Research Agent makes
those judgments from immutable evidence and prior research decisions.

This change removes policy and candidate-specific interpretation from
`build_research_feedback()` while preserving the existing factor evaluation,
Walk-Forward evaluation, incremental model comparison, and Bootstrap
calculations.

## Responsibility Boundaries

### Alpha Lab

Alpha Lab owns reproducible experiment execution and a structured Evidence
Report. The report records only observed values and experiment provenance:

- Run ID and evaluation mode.
- Candidate identity, source hash, snapshot path, and parameters.
- Data identity, date range, test range, label horizon, and sample counts.
- Factor quality, IC, RankIC, monthly stability, and decile results already
  produced by the evaluator.
- Candidate, baseline, and combined prediction-model performance when each is
  applicable.
- Factor similarity against the evaluated baseline set.
- Incremental AUC, LogLoss, Brier, and monthly stability results.
- Bootstrap configuration and results, including the meaning and provenance of
  the recorded statistics without interpreting them as hypothesis truth.
- Walk-Forward, training-window, retraining-window, purge/alignment, and
  development/OOS usage metadata already available from the run.
- Explicit applicability/status fields when an evaluation was not performed.

Alpha Lab must not emit classifications, tags that encode a research verdict,
economic narratives, mechanism claims, or recommended next actions.

### Research Agent

Research Agent consumes one or more Evidence Reports plus historical research
records and creates a Research Decision. A decision contains:

- Evidence supporting the current conclusion.
- Evidence opposing or weakening the current conclusion.
- The Agent's research conclusion.
- The next-round hypothesis.
- The next research action or actions.
- The referenced Run ID and exact candidate source version.
- Creation metadata sufficient to distinguish later decisions.

The Agent may use any research taxonomy it finds useful, but that taxonomy is
not supplied or enforced by Alpha Lab.

## Artifact Contract

### Evidence Report

New completed candidate runs store an `evidence_report` object in the canonical
`summary.json`. It is built from the existing evaluation outputs and run
metadata. The report is objective and self-identifying: `run_id` and candidate
`source_hash` are required when a candidate exists.

`build_research_feedback()` is replaced or renamed to reflect its evidence-only
role. No compatibility alias may retain policy behavior. Existing historical
summaries containing `research_feedback` remain readable, but new runs do not
produce that field.

Evidence Reports are immutable experiment artifacts. Indexing or writing a
Research Decision must never rewrite `summary.json` or any detailed CSV.

### Research Decision

Research Decisions are stored separately from evaluations in an append-only
`research_decisions` table. Each row has a unique decision ID and includes:

- `run_id`, referencing a completed experiment.
- `candidate_id`, referencing the indexed candidate.
- `candidate_source_hash`, copied from the referenced candidate version and
  validated on insertion.
- Structured JSON for supporting evidence, opposing evidence, next hypothesis,
  and next actions.
- A textual or structured conclusion.
- Creation time and an optional Agent/model identity if supplied by the caller.

Insertion fails if the run is absent, incomplete, not indexed, bound to a
different candidate, or has a different source hash. Existing decision rows are
never updated through the public API. A changed opinion creates another row.

The Research Agent, rather than Alpha Lab's experiment runner, calls the
decision-writing interface. Running or indexing an experiment never creates a
Research Decision automatically.

## SQLite Index

The `evaluations` table remains an index of objective measurements. Policy
columns such as `classification`, `tags_json`, and `feedback_json` are no longer
populated for new reports. Additive migration keeps those legacy columns and
their historical values so existing databases remain readable.

Objective Evidence Report data may be stored in a new `evidence_json` column
for compact retrieval, while detailed time series and predictions remain in run
artifacts. Re-indexing the same run may refresh only its objective index row; it
must not alter Research Decisions.

The separate `research_decisions` table references both the experiment and
candidate records. Query APIs return evaluation evidence and decisions as
separate fields rather than merging a decision into the evaluation.

## Compatibility and History

- Old summaries with `research_feedback` continue to index and display their
  legacy fields as historical output.
- New summaries use `evidence_report` and leave legacy policy fields empty.
- Historical `research_feedback` is not automatically converted into a
  Research Decision because it was generated by fixed Alpha Lab policy rather
  than autonomous Agent judgment.
- Existing CLI list/show behavior remains usable; output may expose evidence
  and decisions separately.
- Backfill remains idempotent and must not synthesize decisions.

## Error Handling and Integrity

- Evidence construction fails clearly when required evaluation sections are
  malformed rather than silently inventing zero values.
- Non-applicable measurements remain `null` with an explicit status.
- Decision insertion validates Run ID, completed status, indexed candidate ID,
  and source hash in one transaction.
- Decision payloads must be JSON-serializable and finite; malformed payloads
  are rejected before insertion.
- No decision API accepts an overwrite flag or update-in-place operation.

## Testing Strategy

Tests will establish the new behavior before implementation:

1. Evidence construction contains the required objective sections and contains
   none of the removed classification, action, mechanism, or candidate-name
   policy behavior.
2. Candidate names do not change Evidence Report semantics.
3. Existing factor evaluation, three-model incremental evaluation,
   Walk-Forward behavior, and Bootstrap results remain untouched and continue
   passing their current tests.
4. New-run summaries contain `evidence_report`, Run ID, and source hash but no
   `research_feedback`.
5. Indexing stores objective evidence without creating a decision.
6. Decision creation requires the exact Run ID and source hash, appends multiple
   decisions without replacement, and rejects mismatches or incomplete runs.
7. Legacy summaries and database columns remain readable, and backfill never
   promotes legacy feedback into a decision.

## Out of Scope

- Changing factor formulas, labels, model choice, scoring definitions,
  Walk-Forward splits, Bootstrap algorithms, or thresholds inside those
  evaluators.
- Implementing the Research Agent's reasoning model or enforcing a research
  taxonomy.
- Reinterpreting old policy-generated feedback as human- or Agent-authored
  decisions.
- Rewriting mature evaluation modules solely to accommodate the new report
  boundary.
