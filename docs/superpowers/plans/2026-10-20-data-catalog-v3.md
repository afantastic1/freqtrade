# Data Catalog v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only v3 data catalog that validates and reports real local market-data coverage while preserving v1/v2 behavior.

**Architecture:** Keep `data_engineering.catalog` and its v1/v2 schemas unchanged. Add v3 manifest/contracts/store/query code, a read-only Freqtrade Feather source adapter, and separate CLI subcommands. Store source-root IDs and relative source paths in manifests; partition fingerprints are canonical and deterministic, while source hashes bind each version to immutable bytes.

**Tech Stack:** Python 3.11+, pandas/pyarrow through existing Freqtrade Feather support, jsonschema Draft 2020-12, pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-data-catalog-v3-design.md`

## Global Constraints

- Keep the v1 and v2 JSON Schemas and validation/read paths unchanged.
- Use UTC and half-open `[start, end)` intervals.
- Never fill missing OHLCV, Funding Rate, Open Interest, or Spot observations with zeroes.
- Reject source paths outside configured source roots and reject symlinks.
- Keep registration append-only; a changed source creates a new immutable version.
- Keep catalog queries offline; this PR does not download data.
- Treat sparse Funding Rate and Open Interest as event series; do not infer dense gaps or fill values.
- Make fingerprints deterministic and independent of registration timestamps.

## Review Focus

- A source path escaping its configured root or traversing a symlink must fail; cover in Task 3 path-safety tests.
- Naive timestamps, timestamps exactly on the exclusive end, and unaligned candle times must be handled correctly; cover in Task 4 timestamp tests.
- Duplicate timestamps and invalid OHLCV values must report `INVALID`; cover in Task 4 quality tests.
- An unknown dataset or interval with no observations must report `MISSING`; cover in Task 5 coverage tests.
- Sparse OI/Funding must preserve observed rows without invented zeroes or cadence gaps; cover in Task 5 sparse-series tests.

---

### Task 1: Versioned v3 manifest and data-kind contracts

**Files:**
- Create: `data_engineering/schemas/dataset-manifest-v3.schema.json`
- Create: `data_engineering/data_kind_registry.py`
- Test: `tests/test_data_engineering_catalog_v3.py`
- Modify: `tests/test_data_engineering_packaging.py`

**Interfaces:**
- Produces `DATA_KIND_CONTRACTS` keyed by `ohlcv`, `funding_rate`, `open_interest`, and `mark_price`, each declaring required columns, timestamp column, cadence policy, and value validation identifiers.
- Schema requires `schema_version="data-catalog-v3"`, dataset identity (`exchange`, `market_type`, `asset`, `exchange_symbol`, `data_kind`, and `timeframe` as a string or JSON null for event data), source-root reference, ordered partitions, quality status, and revision metadata.

- [ ] **Step 1: Write failing schema and registry tests** for valid OHLCV/Funding/OI/Spot-Futures identities, unsupported kinds, missing contract fields, malformed partition intervals, and valid v1/v2 schema resources remaining available.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3.py tests/test_data_engineering_packaging.py -q`; confirm the new v3 schema/registry cases fail because the files do not exist.
- [ ] **Step 3: Add the strict Draft 2020-12 v3 schema** and explicit data-kind registry. Do not alter v1/v2 schemas or `SCHEMA_VERSION`.
- [ ] **Step 4: Run the same command** and confirm all schema and packaging tests pass.
- [ ] **Step 5: Commit** with `feat(data): define catalog v3 manifest contracts`.

### Task 2: Append-only catalog store, deterministic identity, and lineage

**Files:**
- Create: `data_engineering/catalog_v3.py`
- Test: `tests/test_data_engineering_catalog_v3.py`

**Interfaces:**
- `validate_v3_manifest(document: dict) -> None`
- `register_dataset(root: Path, manifest: dict) -> dict`
- `list_datasets(root: Path, *, exchange: str | None = None, market_type: str | None = None, asset: str | None = None, data_kind: str | None = None, timeframe: str | None = None) -> list[dict]`
- Manifest versions are addressed by stable dataset ID plus deterministic manifest fingerprint; revisions refer to the previous manifest fingerprint.

- [ ] **Step 1: Write failing tests** for v3-only schema dispatch, deterministic manifest fingerprints excluding registration time, atomic first registration, idempotent repeat registration, immutable changed versions, lineage, deterministic list order, and unchanged legacy `validate_manifest` behavior.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm the v3 API tests fail before implementation.
- [ ] **Step 3: Implement v3 schema loading, canonical JSON hashing, atomic manifest creation, version/revision lookup, and filtered listing** in `catalog_v3.py`.
- [ ] **Step 4: Run** `pytest tests/test_data_engineering_catalog_v3.py tests/test_data_engineering.py -q`; confirm v3 and legacy catalog behavior pass together.
- [ ] **Step 5: Commit** with `feat(data): add append-only catalog v3 store`.

### Task 3: Read-only local Feather discovery and registration

**Files:**
- Create: `data_engineering/freqtrade_feather.py`
- Modify: `data_engineering/catalog_v3.py`
- Test: `tests/test_data_engineering_catalog_v3.py`

**Interfaces:**
- `discover_feather_datasets(source_root_id: str, source_root: Path, *, exchange: str, market_type: str) -> list[dict]` returns candidate identity and source-relative path records without modifying files.
- `register_feather_file(catalog_root: Path, *, source_root_id: str, source_root: Path, relative_path: Path, exchange: str, market_type: str, asset: str, exchange_symbol: str, data_kind: str, timeframe: str | None, source: str, source_reference: str = "") -> dict` inspects and registers a local file.
- Catalog APIs accept `source_roots: Mapping[str, Path]` when source bytes must be verified.

- [ ] **Step 1: Write failing tests** for known spot/futures OHLCV, OI, and funding filename parsing; spot/futures identity; unsupported/ambiguous names; source-root escape; symlink rejection; source SHA-256; and source-file immutability.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm discovery/registration tests fail before the adapter exists.
- [ ] **Step 3: Implement read-only Feather discovery and registration** with explicit source-root IDs, strict resolved-path containment, streamed source hashing, contract inspection, and monthly partition records/fingerprints. Keep original Feather bytes unchanged.
- [ ] **Step 4: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; verify all adapter cases pass and source bytes remain identical.
- [ ] **Step 5: Commit** with `feat(data): catalog local freqtrade feather partitions`.

### Task 4: Data-kind quality validation and integrity verification

**Files:**
- Create: `data_engineering/quality/__init__.py`
- Create: `data_engineering/quality/contracts.py`
- Modify: `data_engineering/catalog_v3.py`
- Test: `tests/test_data_engineering_catalog_v3.py`

**Interfaces:**
- `validate_observations(frame: pandas.DataFrame, *, data_kind: str, timeframe: str | None) -> dict` returns row count, observed interval, cadence summary, quality status, and issue records.
- `validate_dataset(root: Path, dataset_id: str, *, source_roots: Mapping[str, Path]) -> dict` returns manifest, current integrity/quality status, and issues; an unknown ID returns `MISSING`.

- [ ] **Step 1: Write failing tests** for duplicate timestamps, non-UTC/naive times, OHLCV misalignment and gaps, nonfinite/nonpositive prices, impossible high/low relationships, negative volume, invalid Funding Rate/OI/mark-price values, source hash mismatch, and partition fingerprint mismatch.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm the quality/integrity cases fail before implementation.
- [ ] **Step 3: Implement contract validators** and recomputation of source hashes, canonical partition fingerprints, and quality summaries. Invalid data returns `INVALID` with actionable issue codes; validation never repairs or fills data.
- [ ] **Step 4: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm invalid fixtures are rejected and valid contract fixtures remain valid.
- [ ] **Step 5: Commit** with `feat(data): validate catalog v3 data quality`.

### Task 5: Coverage queries and status semantics

**Files:**
- Modify: `data_engineering/catalog_v3.py`
- Create: `data_engineering/dataset_coverage.py`
- Test: `tests/test_data_engineering_catalog_v3.py`

**Interfaces:**
- `get_coverage(root: Path, dataset_id: str, start: datetime, end: datetime, *, source_roots: Mapping[str, Path]) -> dict` is implemented in `dataset_coverage.py` and re-exported by `catalog_v3.py`; it returns requested interval, status, observed intervals, missing intervals/count, partition fingerprints/revisions, and observed cadence.
- Dense OHLCV cadence is `AVAILABLE` only with every expected timestamp in `[start, end)`; intersecting but incomplete data is `PARTIAL`; no observations is `MISSING`; integrity/contract errors are `INVALID`.
- Sparse Funding/OI reports observations and source-frequency facts; it does not synthesize dense gaps or expected values. With no declared event cadence, observations yield `PARTIAL` plus `coverage_basis="observed_only"`; only a declared event cadence can support `AVAILABLE`.

- [ ] **Step 1: Write failing tests** for full and partial OHLCV intervals, leading/trailing/internal gaps, unknown dataset and empty interval, invalid start/end ranges, invalid precedence, exclusive end behavior, bounded gap-list output with total count, sparse OI/Funding with and without declared cadence (no declared cadence reports observed-only `PARTIAL`) and without zero rows.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm coverage cases fail before the API exists.
- [ ] **Step 3: Implement `get_coverage`** using verified manifest partitions, expected candle timestamps for dense contracts, and observed event timestamps for sparse contracts.
- [ ] **Step 4: Run** `pytest tests/test_data_engineering_catalog_v3.py -q`; confirm all status and no-imputation tests pass.
- [ ] **Step 5: Commit** with `feat(data): add v3 dataset coverage queries`.

### Task 6: CLI commands and real-data acceptance

**Files:**
- Modify: `data_engineering/__main__.py`
- Modify: `data_engineering/pyproject.toml` only if packaging requires new resources
- Modify: `tests/test_data_engineering_packaging.py`
- Create: `tests/test_data_engineering_catalog_v3_cli.py`
- Test actual local source: `user_data/data/binance/futures/BTC_USDT_USDT-5m-futures.feather`

**Interfaces:**
- Add `catalog-v3 list`, `catalog-v3 discover`, `catalog-v3 register`, `catalog-v3 coverage`, and `catalog-v3 validate` subcommands under the existing `data-engineering` executable.
- Source and catalog roots are explicit CLI options; all query commands remain offline and read-only.

- [ ] **Step 1: Write failing CLI tests** for JSON output, explicit roots, list/discover/register/coverage/validate routing, invalid inputs, and legacy CLI command compatibility.
- [ ] **Step 2: Run** `pytest tests/test_data_engineering_catalog_v3_cli.py tests/test_data_engineering_packaging.py -q`; confirm the new subcommand cases fail.
- [ ] **Step 3: Implement CLI wiring** and package schema/quality resources without changing legacy command behavior.
- [ ] **Step 4: Run** `pytest tests/test_data_engineering_catalog_v3_cli.py tests/test_data_engineering_packaging.py tests/test_data_engineering_catalog_v3.py -q`.
- [ ] **Step 5: Run actual BTC 5m acceptance** in `/tmp/alpha-lab-data-catalog-v3-acceptance` so the source and regular user catalog remain untouched: discover/register the local futures file; query `[2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)` and a second interval ending one timeframe after the latest observed candle; confirm actual gaps, partition versions, hashes, deterministic fingerprint, and status are reported.
- [ ] **Step 6: Run legacy regression**: `pytest tests/test_data_engineering.py tests/test_data_engineering_packaging.py -q`; confirm v1/v2 manifests still validate and read.
- [ ] **Step 7: Run repository-required freeze/governance checks** and record exact HEAD, commands, outputs, and actual-vs-synthetic data evidence in the PR-02 acceptance report.
- [ ] **Step 8: Commit** with `feat(data): expose catalog v3 coverage cli`.

