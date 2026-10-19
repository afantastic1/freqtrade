# Fundamental Data Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, version-pinned adapter from the Data Engineering Catalog to explicitly aligned Alpha Lab features while keeping current PIT-unverified observations out of strict joins.

**Architecture:** Add versioned static registries and a Catalog-backed adapter under `data_engineering`. Add a separate bridge that aligns verified observations to `MarketPanel` availability times and returns feature Series accepted by `CrossSectionalDataset`; do not edit frozen Alpha Lab evaluation code.

**Tech Stack:** Python, pandas, existing `data_engineering.catalog.verify`, Alpha Lab `MarketPanel` / `CrossSectionalDataset`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-fundamental-data-adapter-design.md`

## Global Constraints

- Research interval is `[2022-01-01, 2025-01-01)` UTC.
- `available_time` remains null if no actual publication/availability value exists.
- Existing fundamental history is PIT-unverified; `strict_pit` rejects it.
- Existing Catalog snapshots and SQLite rows are read-only and append-only.
- Never download, duplicate, rewrite, or republish source data.
- Preserve Freqtrade OHLCV, funding, and open-interest paths.
- Do not modify Alpha Lab statistics, inference, evaluation, Research Decision, or method versions.
- Missing observations remain null; do not fill zero or carry data before its first observation.

## Review Focus

- Entity alias maps token and ecosystem measurements without merging their economic scopes; test registry cross-scope rejection.
- Present-day or future-dated data cannot enter ordinary research; test inclusive start/exclusive end validation and 2025 rejection.
- A claimed publication timestamp without an audit attestation cannot enable strict PIT; test declared-only rejection and audited manifest acceptance.
- Missing symbols/metrics and pre-listing XRP TVL remain null after bridge alignment; test aligned output.
- Catalog paths and manifests can be tampered with or redirected through symlinks; test digest verification and path rejection.

---

### Task 1: Versioned asset and metric registries

**Files:**
- Create: `data_engineering/registries.py`
- Create: `tests/test_fundamental_registries.py`

**Interfaces:**
- `AssetRegistry.resolve_source_id(source: str, source_asset_id: str, *, entity_scope: str | None = None) -> AssetIdentity`
- `MetricRegistry.resolve(source: str, metric: str, entity_scope: str) -> MetricDefinition`
- Registry versions are constants and immutable mapping values.

- [x] Write tests for five tradable assets, Coin Metrics token aliases, DefiLlama ecosystem aliases, non-collapsing `asset_id`/`tradable_asset_id`, protocol scope, and unknown/invalid mappings.
- [x] Run the tests and observe expected import/attribute failures.
- [x] Implement immutable dataclasses and mappings. Register current 10 metric codes with units, definitions, permitted source, and entity scopes. Map DefiLlama `chain:<name>` rows to `ecosystem:<name>`; keep `chain` and `protocol` scopes available for future explicit entries.
- [x] Resolve Coin Metrics network activity/security metrics (`AdrActCnt`, `TxCnt`, `HashRate`) to explicit chain identities, while supply/valuation metrics remain token-scoped.
- [x] Run `tests/test_fundamental_registries.py` and confirm all tests pass.

### Task 2: Read-only, pinned Catalog adapter

**Files:**
- Create: `data_engineering/fundamentals.py`
- Create: `tests/test_fundamentals.py`

**Interfaces:**
- `FundamentalDataAdapter(root: Path = Path("user_data/data_engineering"))`
- `list_snapshots() -> list[SnapshotRef]`
- `read(dataset_id: str, snapshot_sha256: str, *, access: Literal["exploratory", "strict_pit"], start=START, end=END) -> pd.DataFrame`
- `read_many(snapshots: Sequence[SnapshotRef | tuple[str, str]], *, access, start=START, end=END) -> pd.DataFrame`
- Normalized columns: `asset_id`, `tradable_asset_id`, `entity_scope`, `metric`, `event_time`, `available_time`, `value`, `source`, `dataset_id`, `snapshot_sha256`, `pit_status`.

- [x] Write tests that use temporary Catalog snapshots and assert exact normalized identity/provenance columns and bounds.
- [x] Add tests for unknown aliases/metrics, wrong kind, unpinned reads, out-of-range date requests, tampered CSV/manifest, and symlinked snapshot paths. Duplicate rows are rejected by Catalog verification before adapter normalization.
- [x] Verify RED on the adapter tests.
- [x] Implement Catalog discovery and pinned reads by calling existing `verify`; read CSV in place without writing/copying. Reject any date range not wholly inside `[2022-01-01, 2025-01-01)` UTC.
- [x] Parse only actual `available_time` or source `published_at` values; missing values remain `NaT`/null. Do not derive them from event time or lag.
- [x] Require an append-only content-addressed PIT sidecar binding status, reviewer, audit reference, snapshot identity, and availability column before marking records verified. Keep the immutable manifest and legacy `quality.point_in_time` intact; a declared timestamp without attestation remains unverified.
- [x] Make exploratory return unverified rows; make strict mode reject any unverified row or missing actual availability time.
- [x] Run adapter tests and existing Catalog tests.
- [x] Add multi-pin `read_many` and recheck the normalized content fingerprint before joins.

### Task 3: Strict point-in-time join

**Files:**
- Modify: `data_engineering/fundamentals.py`
- Modify: `tests/test_fundamentals.py`

**Interfaces:**
- `asof_join(panel: MarketPanel, observations: pd.DataFrame, *, metric: str, max_age: str) -> pd.DataFrame`
- Accept only records marked verified with non-null actual availability time; match `available_time <= panel.frame.available_time` by registered `tradable_asset_id` and metric.

- [x] Write tests for exact boundary, late publication (future leak), max-age expiration, missing asset, and no pre-first-observation fill.
- [x] Run the join tests and observe expected missing-function failures.
- [x] Implement a left as-of lookup keyed by the MarketPanel symbol mapping; retain unmatched rows as null and include matched snapshot/provenance fields.
- [x] Run join tests and verify an input publication after the decision time never matches.

### Task 4: Alpha Lab feature bridge

**Files:**
- Create: `data_engineering/alpha_lab_features.py`
- Create: `tests/test_fundamental_feature_bridge.py`

**Interfaces:**
- `build_fundamental_features(panel: MarketPanel, observations: pd.DataFrame, *, metrics: Sequence[str], max_age: str) -> dict[str, pd.Series]`
- Output Series index exactly equals `panel.frame.index`; names include entity scope and metric; values come only from strict `asof_join`.

- [x] Test that returned features can be supplied to `CrossSectionalDataset(..., features=...)`.
- [x] Test missing SOL/BNB series, XRP pre-listing TVL, max-age expiration, and duplicate feature identity; assert missing values are null.
- [x] Observe RED, then implement a thin bridge over `asof_join` without editing Alpha Lab source files.
- [x] Run bridge tests and existing MarketPanel/Dataset tests.

### Task 5: Documentation and full verification

**Files:**
- Create: `docs/fundamental_data_adapter.md`

- [x] Document registry meanings, API read examples, access modes, snapshot pinning, join semantics, feature handoff, and why current snapshots fail strict PIT in a new guide. Do not modify the pre-existing untracked `docs/data_engineering.md` because that would add unrelated user-owned v0.1 documentation to this feature commit.
- [x] Run the Fundamental adapter/catalog integration tests (including as-of, Alpha Lab feature bridge, registry, and coverage audit regression tests): 56 passed.
- [x] Run `./.venv/bin/python -m pytest -q alpha_lab/tests/` to verify frozen Alpha Lab behavior remains unchanged: 297 passed.
- [x] Run compileall for `data_engineering` and `alpha_lab`, `git diff --check`, and a read-only 14-snapshot/30-series coverage audit: all passed; errors=0.
- [x] Inspect final staged files and commit only the Adapter implementation, tests, and documentation; exclude existing untracked Data Engineering starter/catalog files and all database/snapshot files.

## Status

- [x] Approved design committed as `b78b156fca`.
- [x] Implementation plan reviewed.
- [x] Implementation and regression tests.
- [x] Final review and separate feature commit (`0982c1f8c4`).
