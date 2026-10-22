# Alpha Lab Data Expansion PR 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strictly read-only Catalog v3 inventory and target-gap planner that proves which Alpha Lab datasets are already validated before any later ingestion work begins.

**Architecture:** A focused `catalog_inventory` module will query immutable Catalog v3 manifests through existing validation and coverage APIs. A new `catalog-v3 audit` CLI subcommand will consume an operator-supplied target JSON file and emit deterministic JSON without downloading, publishing, or modifying data.

**Tech Stack:** Python 3.11+, dataclasses, existing Data Catalog v3 APIs, pytest.

**Spec:** User-approved Alpha Lab MCP data capability audit and phased construction plan from this conversation; no standalone spec file exists.

## Global Constraints

- Preserve the existing seven MCP tools.
- Do not modify frozen statistical inference logic.
- Do not permit Agent writes to research data or the core engine.
- Do not download data that already exists and validates.
- Normalize requested timestamps to explicit UTC intervals.
- PR 0 is read-only: no Catalog registration, source download, normalization, or publication.
- Preserve unrelated dirty-worktree changes.

## Review Focus

- A Catalog dataset whose latest manifest is invalid must be `INVALID`, never `PRESENT`.
- Complete validated coverage must be `PRESENT` so later ingestion skips it.
- Partial coverage must expose missing intervals and must not be `PRESENT`.
- Duplicate target identities must be rejected rather than silently collapsed.
- Target files with unknown fields, naive timestamps, or inconsistent dataset IDs must be rejected.

---

### Task 1: Read-only Catalog target audit

**Files:**
- Create: `data_engineering/catalog_inventory.py`
- Modify: `data_engineering/__main__.py`
- Create: `data_engineering/test_catalog_inventory.py`

**Interfaces:**
- Consumes: `DatasetIdentity`, `validate_dataset`, and `get_coverage` from existing Data Engineering modules.
- Produces: `audit_targets(catalog_root: Path, source_roots: Mapping[str, Path], raw_targets: object) -> dict` and CLI `catalog-v3 audit --targets PATH --source-root ID=PATH`.

- [x] **Step 1: Write failing tests for PRESENT, MISSING, INVALID, partial coverage, duplicate targets, malformed targets, and the read-only CLI response.**

- [x] **Step 2: Run `pytest -q data_engineering/test_catalog_inventory.py` and verify failures are caused by the missing module/subcommand.**

- [x] **Step 3: Implement strict target parsing and deterministic read-only audit output.**

- [x] **Step 4: Run `pytest -q data_engineering/test_catalog_inventory.py` and verify all tests pass.**

- [x] **Step 5: Run the relevant Data Engineering and Alpha Lab suites to detect regressions.**

- [x] **Step 6: Review the diff and record verification evidence; do not commit unrelated files.**

