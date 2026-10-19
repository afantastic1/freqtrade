# Data Engineering v0.1 Reproducibility and PIT Governance Plan

> **For agentic workers:** Follow `superpowers:executing-plans`; each task uses TDD and is completed in order.

**Goal:** Make Data Engineering independently installable from a clean checkout and govern PIT claims with versioned, evidence-bound schemas while leaving all current data UNVERIFIED.

**Architecture:** Track the existing `data_engineering/` package as the canonical runtime and add standalone setuptools metadata plus packaged JSON Schemas. Validate Catalog manifests and content-addressed PIT sidecars against schemas and snapshot rows; generate a metadata-only per-dataset/metric eligibility checklist. Preserve the old Catalog manifest reader and strict fail-closed behavior.

**Tech Stack:** Python 3.11+, setuptools, pandas, jsonschema, pytest, JSON Schema Draft 2020-12.

**Spec:** `docs/superpowers/specs/2026-10-19-data-engineering-pit-governance-design.md`

## Global Constraints

- Preserve all local `user_data/` files, SQLite records, market data, Catalog snapshots, and manifests byte-for-byte.
- Do not inspect any data dated 2025 or later.
- Do not infer availability from event time or fixed delay.
- Keep existing snapshots UNVERIFIED and strict mode fail-closed.
- Do not modify Alpha Lab statistics, evaluation, inference, or historical decisions.
- Do not commit the duplicate `data_engineering_starter/` tree or generated/cache/data files.

## Review Focus

- An existing data-catalog-v1 manifest must continue to verify without rewriting; new data-catalog-v2 manifests identify `ingested_at` explicitly.
- A missing, changed, or wrong-snapshot evidence blob must never upgrade PIT status.
- A claimed fixed lag or `event_time` as availability must fail validation.
- A CONSERVATIVE upper bound later than a decision must not match that decision.
- A clean tree must install the standalone package without relying on the checkout root being importable.

---

### Task 1: Standalone package and catalog schemas

**Files:** `data_engineering/pyproject.toml`, package schemas, `catalog.py`, `__init__.py`, `tests/test_data_engineering.py`.

- [ ] Add tests for standalone package metadata/resource schema access and v1/v2 manifest validation.
- [ ] Run those tests and confirm missing packaging/schema behavior.
- [ ] Add standalone package metadata with CLI entry point and bounded runtime dependencies.
- [ ] Add Draft 2020-12 v1/v2 Catalog manifest schemas and schema-driven publication/verification validation.
- [ ] Export or copy only the canonical runtime package, core tests, docs, and scripts into a temporary clean tree; install and run core tests.

### Task 2: PIT evidence schemas and semantic validation

**Files:** `data_engineering/schemas/pit-evidence-v1.schema.json`, `data_engineering/pit.py`, `tests/test_pit_evidence.py`.

- [ ] Add tests for VERIFIED row coverage/evidence links, CONSERVATIVE upper bounds, UNVERIFIED without proof, REJECTED conflicts, digest tampering, revision identity, and invalid time ordering.
- [ ] Observe the expected failures before implementation.
- [ ] Implement schema validation and semantic binding to one pinned manifest and every source row; check immutable evidence blobs by SHA-256.
- [ ] Require actual `ingested_at` from Catalog metadata and keep it separate from event/availability timestamps.

### Task 3: Adapter and strict as-of integration

**Files:** `data_engineering/fundamentals.py`, `alpha_lab_features.py`, adapter tests.

- [ ] Add tests for all four states, output time/revision columns, strict VERIFIED/CONSERVATIVE acceptance, and strict rejection of UNVERIFIED/REJECTED.
- [ ] Add negative future-leak tests for exact availability and conservative upper bounds.
- [ ] Integrate validated sidecars without mutating Catalog snapshots or manifests; expose effective join availability only for proof-backed states.
- [ ] Verify the legacy un-attested snapshots still return UNVERIFIED and are rejected by strict PIT.

### Task 4: Audit checklist and source qualification

**Files:** `data_engineering/pit_audit.py`, CLI, `docs/data_engineering_pit_audit.md`, tests.

- [ ] Add a metadata-only audit checklist for every fundamental dataset/metric with missing proof fields and strict eligibility.
- [ ] Verify all current Coin Metrics and DefiLlama records remain UNVERIFIED; no source becomes eligible from ingestion time alone.
- [ ] Add table/checksum tests and ensure audit never reads raw or reserved-period data.

### Task 5: Clean-checkout acceptance and commit

- [ ] Run full Data Engineering test suite from a temporary export of HEAD with no `user_data/` copied.
- [ ] Run `compileall`, `git diff --check`, and the read-only 2022–2024 Catalog coverage audit.
- [ ] Confirm no user data, SQLite, cache, Alpha Lab core, or Freqtrade execution files are staged.
- [ ] Commit source, package metadata, schemas, tests, and governance docs as one independent commit.
