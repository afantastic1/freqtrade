# Data Engineering v0.1 Reproducibility and PIT Governance

## Goals and constraints

Version the Data Engineering runtime so a clean checkout can install it and
run its complete test suite. Add a formal Catalog manifest schema and a
versioned PIT evidence contract. Preserve all existing snapshot bytes,
manifests, local databases, Freqtrade market data, and Alpha Lab research
records. Do not read reserve-period data, infer historical publication times,
or change Alpha Lab inference and decision thresholds.

## Repository and packaging

`data_engineering/` is the canonical runtime package. A lightweight
`data_engineering/pyproject.toml` makes it independently installable and
provides the `data-engineering` CLI. Runtime requirements are limited to the
dependencies needed by the adapter (`pandas` and `jsonschema`); pytest is a
test extra. The existing Freqtrade root packaging remains unchanged. The
existing `data_engineering_starter/` is a duplicate installer archive: retain
it locally and do not include its duplicate source tree in the repository.

Track canonical source, CLI, scripts, core tests, user guide, package metadata,
and JSON Schemas. Keep `user_data/`, snapshots, raw/processed files, SQLite,
reports, Python caches, and pytest caches excluded. Existing broad ignore rules
already cover these locations; add explicit comments/rules only if tests show
an artifact can enter the index.

## Catalog manifest schema

Add a Draft 2020-12 schema for `data-catalog-v1` manifests. Require dataset ID,
kind, source, source reference, access class, SHA-256, byte size, quality,
actual ingest/publication timestamp, research-safe flag, and note. Preserve
unknown extension fields for compatibility. Validate the schema in both
publication and verification paths; do not rewrite existing manifests.

## PIT evidence schema and semantics

PIT sidecars use `pit-evidence-v1`, stored content-addressed under
`pit_attestations/<sha256>.json`. Sidecars bind dataset ID, snapshot SHA-256,
source, source schema, actual `ingested_at`, auditor, audit time, and one record
per covered `(asset_id, metric, event_time)` observation. Each record includes
revision identity, status, and evidence references. Every VERIFIED or
CONSERVATIVE row must map exactly once to the snapshot; no missing, duplicate,
or extra record is accepted.

Time fields are distinct:

- `event_time`: time period or event described by the value.
- `available_at`: source publication/availability time supported by evidence.
- `available_at_upper_bound`: proven latest time by which this exact revision
  was available. A conservative join uses this value, never an inferred lag.
- `ingested_at`: actual snapshot ingestion time from the immutable Catalog
  manifest. It is provenance and cannot substitute for historical availability
  in existing snapshots.
- `revision_id`: source revision identifier or explicitly audited immutable
  source-vintage identifier. A snapshot digest alone cannot prove a historical
  vendor revision.

Statuses:

- `VERIFIED`: exact `available_at`, a nonempty revision ID, and traceable
  evidence for every row.
- `CONSERVATIVE`: no exact availability claim; `available_at_upper_bound`, a
  nonempty revision ID, and evidence proving the bound for every row.
- `UNVERIFIED`: evidence is absent or cannot prove historical version/time.
- `REJECTED`: evidence/schema conflicts, identities differ, values/times do not
  match, or time ordering/bounds are invalid.

Evidence references include an identifier, source URI, media type, and SHA-256
of immutable evidence material under `pit_evidence/<sha256>`. The validator
checks file hashes and row-to-reference identity. No source URI fetch occurs
during research or validation. `event_time`, `ingested_at`, and fixed delays
cannot promote UNVERIFIED data.

Strict PIT accepts only valid VERIFIED rows using `available_at`, or valid
CONSERVATIVE rows using `available_at_upper_bound`; the effective time must be
no earlier than `event_time`, no later than actual `ingested_at`, and inside the
development interval to join. Missing proof, UNVERIFIED, REJECTED, or evidence
that only shows a 2026 capture for a 2022–2024 row stays unavailable to the
2022–2024 strict experiment. This uses delayed availability only where proven
and keeps the existing fail-closed behavior.

## Source audit and checklist

Generate a versioned checklist from the existing Catalog metadata without
reading raw files. Group each dataset/metric under its source and report
manifest pin, current state, missing historical publication proof, missing
revision/vintage proof, evidence reference status, and strict research
eligibility. Existing snapshots stay UNVERIFIED because they have no
row-level release history or revision evidence. DefiLlama fetch receipts prove
2026 retrieval only; Coin Metrics source references are raw content digests,
not historical publication evidence. No current source qualifies for strict
2022–2024 research.

## Validation and tests

Use JSON Schema plus semantic validators for manifest identity, sidecar digest,
snapshot row coverage, immutable evidence hashes, revision IDs, availability
ordering, ingestion boundary, and strict eligibility. Negative tests cover
future availability, event-time substitution, fixed-lag claims without
evidence, wrong snapshot/source/revision, changed evidence bytes, duplicate or
missing row entries, and conservative availability beyond a decision time.

Create a temporary export of the committed Git tree, install only
`data_engineering/` into the existing project environment, and run all
Data Engineering tests from that clean tree. Do not copy local `user_data/`.
