# Dynamic Dataset and Panel Builder v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish authorized, reproducible time-series and cross-sectional datasets with split-bounded history, point-in-time inputs, evaluator-only labels, and immutable provenance.

**Architecture:** Add a pinned Catalog v3 reader, trusted source availability rules, a temporal planner, and a request-driven `DatasetBuilderV2`. Keep worker inputs, labels, build reports, and manifests as separately fingerprinted products. Preserve historical Alpha Lab v1.1 hashes while introducing an active content manifest for the new interface.

**Tech Stack:** Python 3.11, pandas, pyarrow, jsonschema, existing ResearchRequest v2/CampaignPolicy v1 and Data Catalog v3 contracts.

**Spec:** `docs/superpowers/specs/2026-10-20-dynamic-dataset-panel-builder-design.md`

## Global Constraints

- Every request and source interval uses UTC and half-open `[start, end)` bounds.
- The selected campaign split bounds warmup, study, and label reads; no cross-split reads are implicit.
- Source matching uses full Catalog identity and trusted campaign authorization, never symbol text alone.
- Worker inputs include only observations with `available_time <= decision_time`; labels and label metadata remain outside worker serialization.
- Unverified availability never enters worker input; conservative timing assumptions are versioned and identified as assumptions.
- Missing data follows exactly `reject` or `preserve`; no interpolation, zero-fill, duplicated, or synthetic observations.
- Spot, Futures, Funding, and OI remain separate typed inputs; auxiliary joins are availability-time as-of joins with explicit staleness.
- Source files, historical research records, and `alpha_lab/freeze-manifest-v1.1.json` remain byte-for-byte unchanged.
- Dataset builds perform no source downloads and never overwrite content-addressed artifacts.
- The new CLI has no fixed 2022–2024 date bound; the legacy `panel_cli` stays available for compatibility.

## Review Focus

- A pinned older Catalog revision or symlinked/tampered partition must be read only when its exact digest and source bytes verify. Pin this in `test_read_dataset_snapshot_rejects_tampered_bytes_and_symlink_source` (Task 1).
- Two datasets with the same symbol text but different market type, exchange symbol, or data kind must not be treated as interchangeable. Pin this in `test_resolver_requires_full_dataset_identity` (Task 6).
- Event time earlier than decision time is insufficient if publication availability is later; the row must not enter worker view. Pin this in `test_asof_excludes_late_event_availability` (Task 2).
- A warmup or label horizon touching an adjacent campaign split must fail or remain missing under the selected policy, never cross the split. Pin this in `test_temporal_plan_never_crosses_selected_split` (Task 3).
- Sparse Funding/OI cadence must not be densified or treated as missing OHLCV bars; stale or unavailable events remain null. Pin this in `test_preserve_keeps_sparse_events_without_fill` (Task 3) and `test_asof_enforces_event_max_age` (Task 2).

---

### Task 1: Read a pinned Catalog v3 snapshot

**Files:**
- Modify: `data_engineering/catalog_v3.py`
- Test: `tests/test_data_engineering_catalog_v3.py`

**Interfaces:**
- Produces `CatalogSnapshotV3(manifest: dict, manifest_sha256: str, observations: pd.DataFrame, quality: dict)`.
- Produces `read_dataset_snapshot(root: Path, dataset_id: str, manifest_sha256: str, start: datetime, end: datetime, *, source_roots: Mapping[str, Path]) -> CatalogSnapshotV3`.
- The function verifies the exact immutable manifest version and all selected partition bytes, then returns only observations in `[start, end)`.

- [x] **Step 1: Write failing tests** named `test_read_pinned_snapshot_returns_exact_half_open_rows`, `test_read_pinned_older_revision_is_stable`, `test_read_dataset_snapshot_rejects_tampered_bytes_and_symlink_source`, and `test_read_dataset_snapshot_rejects_unknown_digest`.
- [x] **Step 2: Run the new tests** with `/home/von/workspace/venv/bin/python -m pytest --noconftest -o addopts='' tests/test_data_engineering_catalog_v3.py -q`; confirmed they fail because the pinned reader was absent.
- [x] **Step 3: Implement the snapshot result and reader** in `data_engineering/catalog_v3.py`, reusing the existing path, manifest, partition fingerprint, and observation validators. Explicit pins are selected directly.
- [x] **Step 4: Run the focused Catalog v3 test file** with the same command; 68 passed.
- [x] **Step 5: Commit** as `feat(data): read pinned catalog snapshots` (`a394b8bba3`).

### Task 2: Define availability evidence and point-in-time joins

**Files:**
- Create: `alpha_lab/data/availability.py`
- Create: `alpha_lab/data/schemas/availability-rule-v1.schema.json`
- Create: `alpha_lab/data/schemas/availability-evidence-v1.schema.json`
- Test: `alpha_lab/tests/test_availability.py`

**Interfaces:**
- Produces immutable `AvailabilityRuleV1` with exact dataset identity, rule ID/version, mode (`source_evidence` or `fixed_delay`), event timestamp field, optional fixed delay, and optional maximum age.
- Produces immutable `AvailabilityEvidenceV1` bound to `dataset_id`, pinned Catalog manifest SHA-256, and unique event-row keys plus UTC `available_time` and evidence status.
- Produces `AvailabilityRuleRegistry.get(rule_id: str, *, dataset_identity: data_engineering.sources.contracts.DatasetIdentity) -> AvailabilityRuleV1`.
- Produces `assign_availability(observations: pd.DataFrame, identity: DatasetIdentity, manifest_sha256: str, rule: AvailabilityRuleV1, evidence: AvailabilityEvidenceV1 | None) -> pd.DataFrame` with `event_time`, `available_time`, `pit_status`, and provenance columns.
- Produces `asof_join(decisions: pd.DataFrame, observations: pd.DataFrame, *, decision_time: str, max_age: pd.Timedelta | None) -> pd.DataFrame`; every match must satisfy availability `<=` decision time and the staleness bound.

- [x] **Step 1: Write failing tests** named `test_fixed_delay_ohlcv_uses_close_time_plus_registered_delay`, `test_evidence_must_match_pinned_dataset_and_every_row`, `test_asof_excludes_late_event_availability`, `test_asof_enforces_event_max_age`, `test_unverified_event_is_rejected_from_worker_view`, and `test_evidence_rows_reject_duplicate_or_naive_times`.
- [x] **Step 2: Run** `/home/von/workspace/venv/bin/python -m pytest --noconftest -o addopts='' alpha_lab/tests/test_availability.py -q`; confirmed import failure before implementation.
- [x] **Step 3: Implement** schemas, UTC validation, immutable rule lookup, digest-bound row evidence checks, fixed-delay derivation, and availability-time as-of joins.
- [x] **Step 4: Run the focused availability tests**; 6 passed.
- [x] **Step 5: Commit** as `feat(alpha-lab): add source availability contracts` (`fd11799aa0`).

### Task 3: Plan intervals and explicit missingness

**Files:**
- Create: `alpha_lab/data/temporal_plan.py`
- Create: `alpha_lab/data/build_options.py`
- Test: `alpha_lab/tests/test_temporal_plan.py`

**Interfaces:**
- Produces `TemporalPlanV1` with selected split bounds, study bounds, warmup bounds, and label-extension bounds.
- Produces `build_temporal_plan(request: ResearchRequestV2, policy: CampaignPolicyV1) -> TemporalPlanV1`.
- Produces `DatasetBuildOptionsV1(missing_data_policy: Literal['reject', 'preserve'], primary_data_type: str | None, source_snapshot_pins: Mapping[str, str], availability_rule_ids: Mapping[str, str], max_age_by_data_type: Mapping[str, int])`.
- `primary_data_type` must identify one requested Spot/Futures OHLCV source; if omitted, the request must contain exactly one OHLCV type. Funding/OI-only requests fail because they do not define a market decision grid or forward-return labels.
- `warmup_start = max(split.start, request.start - (lookback_bars + 1) * step)`; the extra source slot supplies the first decision in the requested interval because OHLCV becomes available after bar close. The builder reports clipped lookback at split boundaries and does not extend earlier to compensate for gaps.
- Label bounds include the bar at `label_horizon_bars` for the last study decision and may not exceed the selected split end.
- Produces `partition_temporal_samples(labels: pd.DataFrame, *, train_end: pd.Timestamp, test_end: pd.Timestamp, timeframe: str, embargo_bars: int) -> tuple[pd.DataFrame, pd.DataFrame]` using label availability for training eligibility and an embargo offset for test start.

- [x] **Step 1: Write failing tests** covering half-open study bounds, exact lookback slots, split edges, label extension, label availability purge, embargo, missing slots, and sparse events.
- [x] **Step 2: Run** the focused file; confirmed initial missing module import.
- [x] **Step 3: Implement** UTC interval planning, validated build options, missing-slot reporting, and embargo partitioning; sparse events are not expanded.
- [x] **Step 4: Run the focused temporal tests**; 7 passed.
- [x] **Step 5: Commit** as `feat(alpha-lab): plan authorized dataset intervals` (`95ae24a1b3`).

### Task 4: Define separate worker, evaluator, and report products

**Files:**
- Create: `alpha_lab/data/build_products.py`
- Test: `alpha_lab/tests/test_build_products.py`

**Interfaces:**
- Produces `WorkerInputV2(frame: pd.DataFrame, availability_columns: tuple[str, ...])` with `asof(decision_time: pd.Timestamp) -> pd.DataFrame`.
- Produces `EvaluatorLabelsV1(frame: pd.DataFrame)` with label value, decision, entry, end, availability, and validity fields.
- Produces `DatasetBuildReportV1(sources: tuple[dict, ...], issues: tuple[dict, ...], quality: dict)`.
- Produces `DatasetArtifactV1(worker_path: Path, labels_path: Path, report_path: Path, manifest_path: Path, fingerprints: Mapping[str, str])`.
- Produces `DatasetBuildResultV2(worker_input: WorkerInputV2, evaluator_labels: EvaluatorLabelsV1, report: DatasetBuildReportV1, manifest: dict, artifact: DatasetArtifactV1 | None = None)`; the artifact is populated by `DatasetBuilderV2.build` after publication.

- [x] **Step 1: Write failing tests** named `test_worker_asof_contains_only_available_rows`, `test_worker_product_has_no_label_columns`, `test_asof_rejects_naive_or_out_of_range_time`, `test_evaluator_labels_are_index_aligned`, and `test_products_defensively_copy_frames`.
- [x] **Step 2: Run** the focused product file; confirmed initial missing module import.
- [x] **Step 3: Implement** immutable result wrappers with defensive copies and strict index/time validation. Evaluator labels remain outside the worker product.
- [x] **Step 4: Run the focused product tests**; 5 passed.
- [x] **Step 5: Commit** as `feat(alpha-lab): separate worker inputs and labels` (`2685c66f1e`).

### Task 5: Publish immutable dataset manifests and Parquet products

**Files:**
- Create: `alpha_lab/data/dataset_manifest.py`
- Create: `alpha_lab/data/schemas/alpha-lab-dataset-manifest-v1.schema.json`
- Test: `alpha_lab/tests/test_dataset_manifest.py`

**Interfaces:**
- Produces `dataset_manifest_fingerprint(document: dict) -> str`, excluding only volatile creation time.
- Produces `publish_dataset(result: DatasetBuildResultV2, output_root: Path) -> DatasetArtifactV1`.
- `DatasetArtifactV1` returns the immutable worker Parquet path, evaluator-label Parquet path, report path, manifest path, and their SHA-256 fingerprints.
- Artifact paths are content-addressed and published atomically with no-overwrite semantics.

- [x] **Step 1: Write failing tests** covering provenance pins, volatile time, deterministic publish, no-overwrite, and symlink rejection.
- [x] **Step 2: Run** the focused manifest test file; confirmed initial missing module import.
- [x] **Step 3: Implement** schema validation, canonical fingerprints, deterministic Parquet output, safe atomic artifact creation, and manifest publication.
- [x] **Step 4: Run the focused manifest tests**; 5 passed.
- [x] **Step 5: Commit** as `feat(alpha-lab): publish immutable dataset artifacts` (`274d207024`).

### Task 6: Resolve authorized sources and build time-series datasets

**Files:**
- Create: `alpha_lab/data/source_resolver.py`
- Create: `alpha_lab/data/dataset_builder.py`
- Test: `alpha_lab/tests/test_source_resolver.py`
- Test: `alpha_lab/tests/test_dataset_builder_ts.py`

**Interfaces:**
- Produces `ResolvedSourceV1(identity: DatasetIdentity, manifest_sha256: str, observations: pd.DataFrame, availability: pd.DataFrame, quality: dict)`.
- Produces `DatasetSourceResolver.resolve(request: ResearchRequestV2, plan: TemporalPlanV1, options: DatasetBuildOptionsV1) -> tuple[ResolvedSourceV1, ...]`.
- Produces `UniverseDefinition` protocol in `alpha_lab/data/build_products.py` with `universe_hash: str` and `membership(panel: MarketPanel) -> pd.Series`; existing `FixedUniverse`, `HistoricalUniverse`, and `DynamicUniverse` satisfy it.
- Produces `DatasetBuilderV2(policy_registry: CampaignPolicyRegistry, resolver: DatasetSourceResolver, availability_registry: AvailabilityRuleRegistry, output_root: Path)`.
- Produces `DatasetBuilderV2.build(request: ResearchRequestV2, *, options: DatasetBuildOptionsV1, universe: UniverseDefinition | None = None) -> DatasetBuildResultV2`. It publishes through Task 5's `publish_dataset` before returning and includes the immutable artifact reference.
- Dataset type mapping is exact: `futures_ohlcv` -> `market_type=futures,data_kind=ohlcv`; `spot_ohlcv` -> `market_type=spot,data_kind=ohlcv`; Funding and OI map to their same-named `data_kind` and exact registered exchange/asset identity.
- `primary_data_type` selects the OHLCV decision grid when both Spot and Futures are requested. If omitted, accept exactly one requested OHLCV type; reject ambiguity and reject requests with no OHLCV decision grid.
- The TS path adapts verified OHLCV into `MarketData`, computes current forward-return labels only from the bounded label extension, and returns worker/evaluator products separately.

- [x] **Step 1: Write resolver tests** covering full identity, data-type mapping, explicit pins, authorization, gaps, and unverified availability.
- [x] **Step 2: Write TS builder tests** covering BTC construction, product separation, future perturbation, and split-bounded label extension.
- [x] **Step 3: Run both focused files**; confirmed missing imports before implementation.
- [x] **Step 4: Implement** exact identity mapping, request revalidation, pinned Catalog reads, primary-grid selection, availability alignment, missingness, TS labels, manifest assembly, and atomic publication through Task 5.
- [x] **Step 5: Run resolver and TS builder tests plus existing loader/label regressions**; 33 passed.
- [x] **Step 6: Commit** as `feat(alpha-lab): build request-scoped time-series datasets` (`56ca9e353c`).

### Task 7: Build point-in-time cross-sectional panels

**Files:**
- Modify: `alpha_lab/data/dataset_builder.py`
- Modify: `alpha_lab/data/build_products.py`
- Test: `alpha_lab/tests/test_dataset_builder_cs.py`

**Interfaces:**
- Adds `DatasetBuilderV2.build(..., universe: FixedUniverse | HistoricalUniverse | DynamicUniverse) -> DatasetBuildResultV2` for `mode='cross_sectional'`.
- Produces membership aligned exactly to the `MarketPanel` `(open_time, symbol)` index.
- Uses existing `cross_sectional_labels` and `CrossSectionalDataset` semantics; source and membership fingerprints enter the manifest.

- [x] **Step 1: Write failing tests** named `test_builder_cs_builds_eth_btc_1h_request`, `test_historical_membership_excludes_not_yet_listed_assets`, `test_dynamic_membership_uses_only_available_history`, `test_cs_labels_align_to_panel_and_remain_evaluator_only`, and `test_missing_membership_follows_build_policy`.
- [x] **Step 2: Run** `/home/von/workspace/venv/bin/python -m pytest --noconftest -o addopts='' alpha_lab/tests/test_dataset_builder_cs.py -q`; confirm CS builder path is absent.
- [x] **Step 3: Implement** multi-asset resolution, PIT universe membership, panel labels, missing membership reporting, and CS manifest fingerprints.
- [x] **Step 4: Run CS tests plus existing panel/universe/cross-sectional label regressions**: `/home/von/workspace/venv/bin/python -m pytest --noconftest -o addopts='' alpha_lab/tests/test_dataset_builder_cs.py alpha_lab/tests/test_market_panel.py alpha_lab/tests/test_cross_sectional_dataset.py alpha_lab/tests/test_cross_sectional_labels.py -q`; 17 passed.
- [x] **Step 5: Commit** as `feat(alpha-lab): build point-in-time cross-sectional datasets` (`183aae42c5`).

### Task 8: Add a generic request-driven dataset CLI

**Files:**
- Create: `alpha_lab/dataset_cli.py`
- Create: `alpha_lab_contracts/campaigns/dataset_builder_acceptance-v1.json`
- Test: `alpha_lab/tests/test_dataset_cli.py`
- Test: `alpha_lab_contracts/tests/test_dataset_builder_acceptance_policy.py`
- Modify: `alpha_lab/README.md`

**Interfaces:**
- Produces `main(argv: Sequence[str] | None = None) -> int` with `dataset build --request PATH --catalog-root PATH --source-root ID=PATH --output-root PATH --missing-policy reject|preserve`.
- Parses ResearchRequest v2 only through `ResearchRequestV2.from_dict` and the trusted campaign registry; it never accepts an arbitrary policy document as authorization.
- Calls `DatasetBuilderV2.build(...)` with the explicit output root; the returned result already contains its immutable artifact reference.
- Uses the generic request `start`/`end`; leaves frozen `panel_cli.py` and its historical behavior unchanged.

- [x] **Step 1: Add the trusted acceptance campaign** for BTCUSDT/ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT, 5m/1h, TS/CS, and all four supported data types; set discovery `[2022-01-01, 2025-01-01)`, validation `[2025-01-01, 2026-01-01)`, and holdout `[2026-01-01, 2027-01-01)`, calculate its canonical policy hash, and add `test_dataset_builder_acceptance_policy_is_valid`.
- [x] **Step 2: Write failing CLI tests** named `test_dataset_cli_builds_dynamic_2026_request`, `test_dataset_cli_accepts_2025_request`, `test_dataset_cli_rejects_unauthorized_request`, `test_dataset_cli_requires_explicit_missing_policy`, and `test_dataset_cli_does_not_mutate_panel_cli_compatibility`.
- [x] **Step 3: Run** `/home/von/workspace/venv/bin/python -m pytest --noconftest -o addopts='' alpha_lab_contracts/tests/test_dataset_builder_acceptance_policy.py alpha_lab/tests/test_dataset_cli.py -q`; confirmed the policy fixture and command failed before implementation.
- [x] **Step 4: Implement** the request-driven CLI, JSON summary of manifest/artifact identities, explicit configured roots, trusted policy/rule registry wiring, and concise error exits.
- [x] **Step 5: Run CLI tests** and verify legal 2025/2026 requests route without a year constant.
- [x] **Step 6: Commit** as `feat(alpha-lab): expose dynamic dataset build CLI` (`25481487e6`).

### Task 9: Version the active frozen-core and governance manifests

**Files:**
- Create: `alpha_lab/freeze-manifest-core-v1.2.json`
- Create: `alpha_lab/freeze-manifest-v1.4.json`
- Create: `alpha_lab_service/protocol-manifest-v3.json`
- Modify: `alpha_lab_service/manifest.py`
- Modify: `alpha_lab_service/governance.py`
- Modify: `alpha_lab_service/protected-files.json`
- Modify: `.github/workflows/alpha-lab-freeze.yml`
- Modify: `.github/CODEOWNERS`
- Test: `alpha_lab_service/tests/test_manifest.py`

**Interfaces:**
- Keep `alpha_lab/freeze-manifest-v1.1.json` byte-identical and continue checking every historical path/hash in it.
- Add `alpha_lab/freeze-manifest-core-v1.2.json` using the existing Alpha Lab core manifest builder for the complete current Python core, including builder modules, CLI, and tests.
- Extend `alpha_lab_service.manifest` with service v3 coverage of the full `alpha_lab/` tree, including JSON schemas, plus service v2 files and `docs/alpha_lab/dynamic_dataset_builder_acceptance.md`; exclude content-manifest files to avoid self-reference. The v3 protocol manifest tracks the updated manifest/governance code. The v1.4 governance manifest tracks the new core manifest.
- Add governance manifest `1.4-governance` and make `alpha_lab_service.governance.verify_all()` validate historical v1.1 core hashes, active core v1.2, v1.4 governance, and service v3. Historical v1.1 verification checks its original paths and metadata but permits additions covered by v1.2.
- The freeze workflow verifies the active core manifest explicitly; Code Owners and required CI checks point to the active manifests while preserving v1.1 historical records.

- [x] **Step 1: Write failing manifest tests** covering historical hashes, active core coverage, schema coverage, governance coverage, and tamper detection.
- [x] **Step 2: Run manifest tests**; confirmed active core and governance manifests were absent.
- [x] **Step 3: Implement** additive core manifest generation/verification, governance v1.4 selection, protected-file entries, owner entries, and CI invocation. Preserved the v1.1 manifest bytes.
- [x] **Step 4: Run manifest tests, `/home/von/workspace/venv/bin/python -m alpha_lab.freeze --manifest alpha_lab/freeze-manifest-core-v1.2.json`, `/home/von/workspace/venv/bin/python -m alpha_lab_service.manifest --manifest alpha_lab_service/protocol-manifest-v3.json --manifest-version 3.0-python-factor-service`, and `/home/von/workspace/venv/bin/python -m alpha_lab_service.governance`; historical hashes unchanged and active manifests valid.
- [x] **Step 5: Commit** as `chore(alpha-lab): version dataset builder freeze manifests` (`6b70bd51e2`).

### Task 10: Run checklist acceptance and record evidence

**Files:**
- Create: `docs/alpha_lab/dynamic_dataset_builder_acceptance.md`
- Modify: `docs/superpowers/plans/2026-10-20-dynamic-dataset-panel-builder.md`

**Interfaces:**
- Acceptance report records exact HEAD, selected request/policy hashes, source manifest pins, actual versus synthetic evidence, commands, outputs, and every PR-04 checklist result.
- No source dataset, historical Evidence/Decision, or v1.1 manifest is modified.

- [x] **Step 1: Run synthetic boundary/PIT/security set**: 68 passed; includes point-in-time joins, split bounds, embargo, unauthorized sources, missing primary rejection, all four input types, and CLI acceptance policy.
- [x] **Step 2: Run compatibility and package regressions**: 115 passed.
- [x] **Step 3: Run acceptance builds** for BTC 2026 5m, ETH/BTC 2025 1h, and the pinned five-asset 2022–2024 1h panel; exact results and source pins are in the acceptance report.
- [x] **Step 4: Verify future perturbation, split boundaries, embargo, unauthorized-data rejection, and all four source types** from recorded test output and dataset fingerprints. Future perturbation is checked on as-of worker frames, not a downstream factor run.
- [x] **Step 5: Run governance/freeze verification, `compileall`, and `git diff --check`; 496 passed and 10 service tests require unavailable immutable worker image configuration.**
- [x] **Step 6: Commit** the acceptance report and completed plan as `docs(data): record dynamic dataset builder acceptance`.

## Plan Self-Review

- **Spec coverage:** request/policy authorization and full source identity are in Tasks 1, 2, and 6; split bounds, warmup, labels, and embargo are in Task 3; point-in-time products are in Tasks 2, 4, 6, and 7; Spot/Futures/Funding/OI resolution is in Task 6; historical membership is in Task 7; missingness is in Tasks 2, 3, 6, and 7; immutable manifests and artifacts are in Task 5; generic CLI and date coverage are in Task 8; governance and acceptance are in Tasks 9 and 10.
- **Step scan:** every task uses failing tests, a focused implementation, focused verification, and a commit. All public producer interfaces are named with parameter and return types.
- **Type consistency:** Task 6's `DatasetBuildResultV2` consumes the products from Task 4 and the manifest publisher from Task 5; Task 7 extends the same `DatasetBuilderV2`; Task 8 invokes its `build` method.
- **Review Focus coverage:** each of the five focused failure modes maps to named tests in Tasks 1–3 and 6–7.
- **Scope/proportion:** ten sequential, independently reviewable tasks implement one builder subsystem and its required governance/acceptance; worker isolation and evaluation remain outside PR-04 as the spec requires.
