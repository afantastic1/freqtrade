# Data Source Adapters and Incremental Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared local/Vision source-adapter workflow that safely plans, validates, and publishes immutable monthly Parquet with reproducible lineage.

**Architecture:** Keep source-specific discovery and acquisition behind `DataSourceAdapter`; normalize both source families through shared contracts and quality checks. Use staged CLI operations with digest-bound validation receipts and resumable per-month journals, then register immutable Parquet in additive Catalog v3 snapshots.

**Tech Stack:** Python, pandas, pyarrow, existing Freqtrade Feather handlers, urllib/XML/zipfile Vision importer, pytest, JSON Schema, existing `data-engineering` CLI.

**Spec:** `docs/superpowers/specs/2026-10-20-data-source-adapters-design.md`

## Global Constraints

- Preserve ResearchRequest v2, CampaignPolicy v1, legacy catalog v1/v2, and existing Data Catalog v3 readable contracts.
- Use UTC half-open `[start, end)` request bounds.
- Do not fill, deduplicate, reorder before source-order validation, or invent source observations.
- Keep raw Feather and Vision ZIP inputs immutable.
- Vision crypto symbols are only BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT; 1m is explicit BTC/ETH opt-in only.
- Only bounded S3 metadata listing and the fixed Vision archive/checksum host are allowed; validate redirect destinations.
- Dry-run performs metadata discovery only; it does not fetch archive payloads or write files.
- Publish immutable monthly Parquet and append-only complete Catalog v3 snapshots.
- Keep download/publish local CLI only; add no MCP, Agent, web, worker, or campaign-triggered download surface.

## Review Focus

- A monthly slice of a multi-year local sparse Feather file must preserve only that month and must not make other months appear complete — test a multi-month Funding/OI source.
- An invalid latest catalog revision must surface `INVALID`, never silently fall back to an older snapshot — test latest-revision corruption.
- A ZIP with absent checksum metadata must be `CHECKSUM_FAILED`, while confirmed ZIP absence is `UNAVAILABLE` — test both branches.
- A validated source or Parquet object changed before publication must be rejected; a published object without catalog registration must recover registration — test digest binding and journal recovery.
- Existing Feather coverage must not suppress normalized Parquet conversion, and sparse events must not be assigned invented cadence — test local planning semantics.

---

### Task 1: Shared source contracts and stable identity

**Files:**
- Create: `data_engineering/sources/contracts.py`
- Create: `data_engineering/sources/__init__.py`
- Test: `tests/test_data_engineering_sources_contracts.py`
- Modify: `data_engineering/pyproject.toml` to include the new `data_engineering.sources` package in the standalone wheel.
- Modify: `data_engineering/catalog_v3.py` only if existing identity helpers need a public compatibility wrapper.

**Interfaces:**
- Produces `DatasetIdentity`, `DatasetRequest`, `SourceArtifact`, `ObservationBatch`, `DownloadResult`, `ValidationReceipt`, and typed stage/status enums.
- `DatasetIdentity.dataset_id() -> str` preserves the PR-02 identity inputs; range, request ID, and source locator are excluded.
- `DataSourceAdapter` protocol exposes `name`, `capabilities`, `discover(request)`, `read(artifact)`, and capability-checked `download(artifact, destination)`.

- [x] **Step 1: Write failing contract tests** for stable ID across different request ranges, identity changes for symbol/kind/schema changes, aware UTC bounds, explicit unsupported download, and required artifact/receipt fields.
- [x] **Step 2: Run `pytest tests/test_data_engineering_sources_contracts.py -q` and confirm the new contract tests fail.** Initial system pytest could not load `xdist`; the approved workspace Python produced the expected missing-module collection failure before implementation.
- [x] **Step 3: Implement the typed value objects and runtime-checkable protocol** in `data_engineering/sources/contracts.py`; use immutable dataclasses and reject naive/non-UTC bounds.
- [x] **Step 4: Re-run the focused contract tests and PR-02 identity compatibility tests; confirm they pass.** `--noconftest` run: 10 passed, including the PR-02 settlement-symbol identity test.
- [ ] **Step 5: Commit** as `feat(data): add source adapter contracts`.

### Task 2: Local Freqtrade Feather adapter

**Files:**
- Create: `data_engineering/sources/freqtrade.py`
- Test: `tests/test_data_engineering_source_freqtrade.py`
- Modify: `data_engineering/freqtrade_feather.py` only to expose a lossless raw-read option if the adapter cannot bypass cleanup through the current API.

**Interfaces:**
- Consumes Task 1 contracts.
- Produces `FreqtradeFeatherAdapter(source_roots, catalog, dataset_scope)` implementing `discover(request) -> list[SourceArtifact]` and `read(artifact) -> ObservationBatch`.
- Each artifact is an exact monthly bounded view of a potentially multi-year Feather file; artifact bounds are the month/request intersection, not the whole file bounds.

- [x] **Step 1: Write failing tests** for Spot/Futures OHLCV, funding, OI, and mark-price close mapping; exact monthly slicing of a multi-month sparse Feather source; duplicate and out-of-order preservation; unchanged source bytes; and symlink/traversal rejection.
- [x] **Step 2: Run `pytest tests/test_data_engineering_source_freqtrade.py -q` and confirm the adapter tests fail.** Expected missing-module collection failure observed before implementation.
- [x] **Step 3: Implement read-only local discovery and bounded artifact reads** using native Freqtrade decoding where correct, while avoiding cleanup/fill/dedup and preserving the original row order.
- [x] **Step 4: Run the focused adapter tests plus relevant `tests/test_data_engineering_catalog_v3.py` cases; confirm they pass.** `--noconftest` run: 67 passed.
- [x] **Step 5: Commit** as `feat(data): add local Feather source adapter`.

### Task 3: Binance Vision crypto adapter

**Files:**
- Create: `data_engineering/sources/binance_vision.py`
- Modify: `scripts/binance_vision_tradfi.py` to expose reusable parser/discovery functions without changing existing CLI behavior.
- Test: `tests/test_data_engineering_source_vision.py`
- Test: `scripts/test_binance_vision_tradfi.py`

**Interfaces:**
- Consumes Task 1 contracts and the existing importer parser/checksum helpers.
- Produces `BinanceVisionAdapter(http_client, raw_root, allow_1m=False)` with metadata-only `discover`, checksum-verified `download`, and parsed `read` operations.
- Crypto allowlist is fixed to BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT; 1m requires explicit opt-in and BTCUSDT or ETHUSDT.

- [x] **Step 1: Add failing tests** for allowlist isolation from TradFi, 1m opt-in rules, safe S3 listing pagination, fixed archive host, redirect rejection, ZIP 404 vs missing checksum vs transport failure, checksum mismatch, size caps, parser bounds, and no archive payload fetch during discovery.
- [x] **Step 2: Run `pytest tests/test_data_engineering_source_vision.py scripts/test_binance_vision_tradfi.py -q` and confirm new tests fail while existing parser expectations remain captured.** New adapter import failed as expected; first integrated run exposed an importer monkeypatch compatibility issue, subsequently fixed.
- [x] **Step 3: Extract reusable importer operations and implement the crypto adapter** without altering existing importer command behavior.
- [x] **Step 4: Run the focused Vision tests and existing importer suite; confirm all pass.** Combined focused set: 32 passed.
- [x] **Step 5: Commit** as `feat(data): add Binance Vision crypto adapter`.

### Task 4: Planning, validation receipts, and resumable stages

**Files:**
- Create: `data_engineering/sources/pipeline.py`
- Create: `data_engineering/sources/journal.py`
- Test: `tests/test_data_engineering_source_pipeline.py`

**Interfaces:**
- Consumes Tasks 1–3.
- Produces `plan(request, adapter, catalog) -> SourcePlan`, `validate(artifact, adapter, policy) -> ValidationReceipt`, and resumable per-artifact stage operations.
- Plan statuses include `PRESENT`, `MISSING`, `UNAVAILABLE`, `INVALID`, and `PLANNED`; execution statuses include the spec's download/checksum/parse/quality/publish failures.
- Receipt binds source SHA-256, normalized SHA-256, dataset identity, exact bounds, adapter/converter version, and schema version.

- [x] **Step 1: Write failing pipeline tests** for gap/month planning, no download/write in dry-run, normalized-only PRESENT semantics (existing Feather never suppresses conversion), sparse event completeness, cadence/duplicate/numeric/OHLCV validation, bound validation, latest invalid revision behavior, source change after validation, and journal restart states.
- [x] **Step 2: Run `pytest tests/test_data_engineering_source_pipeline.py -q` and confirm the stage semantics fail.** Missing pipeline import was observed before implementation.
- [x] **Step 3: Implement per-month planning, source-preserving validation, digest-bound receipts, and journal states `DISCOVERED`, `DOWNLOADED`, `VALIDATED`, `OBJECT_PUBLISHED`, `CATALOG_REGISTERED` plus terminal failures.** An invalid latest catalog revision is returned as `INVALID`; do not fall back silently.
- [x] **Step 4: Run the focused pipeline tests; confirm dry-run has no payload requests and recovery finishes catalog registration without claiming premature presence.** Pipeline: 15 passed; combined new source and catalog tests: 107 passed.
- [ ] **Step 5: Commit** as `feat(data): add staged source validation pipeline`.

### Task 5: Immutable Parquet publication and Catalog v3 snapshots

**Files:**
- Modify: `data_engineering/catalog_v3.py`
- Modify: `data_engineering/schemas/dataset-manifest-v3.schema.json`
- Create: `data_engineering/sources/parquet.py`
- Test: `tests/test_data_engineering_catalog_v3.py`
- Test: `tests/test_data_engineering_source_publish.py`
- Modify: `data_engineering/pyproject.toml` to declare `pyarrow>=14`.

**Interfaces:**
- Consumes Task 4 validation receipts.
- Produces `publish(receipt, batch, roots, catalog) -> PublishResult` with content-addressed normalized object path and provenance key.
- A new manifest revision is a complete snapshot carrying forward verified, non-overlapping partition references.

- [ ] **Step 1: Write failing tests** for deterministic normalized fingerprints, distinct provenance object keys, optional `storage_format="parquet"` compatibility, raw-source provenance, full-snapshot carry-forward, atomic no-overwrite, symlink-safe roots, object-present/catalog-missing recovery, digest recheck, and incompatible partial overlap rejection.
- [ ] **Step 2: Run the focused catalog and publisher tests and confirm the new Parquet paths fail.**
- [ ] **Step 3: Implement Parquet write/read/hash validation, additive manifest fields, complete append-only snapshots, and atomic publish with registration recovery.**
- [ ] **Step 4: Run focused tests and existing catalog v1/v2/v3 tests; confirm old Feather manifests retain their interpretation and Parquet coverage validates.**
- [ ] **Step 5: Build the standalone data-engineering wheel and confirm metadata includes `pyarrow>=14`.**
- [ ] **Step 6: Commit** as `feat(data): publish immutable provenance-bound Parquet`.

### Task 6: CLI, docs, and acceptance

**Files:**
- Modify: `data_engineering/__main__.py`
- Create: `data_engineering/sources/cli.py`
- Modify: `data_engineering/pyproject.toml` if console entry points are required.
- Create: `docs/alpha_lab/data_catalog_v3_source_adapters_acceptance.md`
- Test: `tests/test_data_engineering_source_cli.py`
- Test: `tests/test_data_engineering_packaging.py`

**Interfaces:**
- Consumes Tasks 1–5.
- CLI commands: `sources discover`, `sources plan --dry-run`, `sources download`, `sources validate`, `sources publish`, and `sources sync`; all require explicit roots and dataset selectors.
- Download and publish are local operator commands only; do not register MCP/Agent/web/worker actions.

- [ ] **Step 1: Write failing CLI tests** proving dry-run makes metadata-only calls and writes nothing; commands require explicit selection/roots; unsupported local download fails; stage statuses and resumability are represented; no external agent interface is added.
- [ ] **Step 2: Run `pytest tests/test_data_engineering_source_cli.py -q` and confirm the commands are absent.**
- [ ] **Step 3: Implement CLI orchestration** delegating all work to the adapter/pipeline APIs and keeping the legacy CLI intact.
- [ ] **Step 4: Run CLI, packaging, existing importer, catalog, and Data Engineering test suites; confirm compatibility and governance checks pass.**
- [ ] **Step 5: If the configured Vision host is available, run one bounded acceptance for an explicitly reviewed crypto monthly archive and record exact URL/checksum/converter/fingerprint/status; otherwise record the exact unavailable condition without substituting synthetic evidence.**
- [ ] **Step 6: Commit** as `feat(data): expose staged source preparation CLI`.

### Final verification

- [ ] Run the full Data Engineering and catalog tests plus existing importer regressions.
- [ ] Run governance/freeze checks, `compileall`, package build/metadata inspection, and `git diff --check`.
- [ ] Verify no Feather or raw ZIP source changed and no MCP/Agent download or publish operation was exposed.
- [ ] Run `superpowers:verification-before-completion` before claiming completion.
