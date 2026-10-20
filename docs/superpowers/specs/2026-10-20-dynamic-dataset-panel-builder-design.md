# PR-04: Dynamic Dataset and Panel Builder v2

## Status

Design approved in conversation on 2026-10-20. This document defines the PR-04 architecture. It is not an implementation plan and does not authorize PR-05 or later work.

## Purpose

Build reproducible time-series and cross-sectional research datasets from an authorized ResearchRequest v2. The builder must plan the exact source interval, resolve and pin immutable Data Catalog v3 snapshots, enforce point-in-time availability, keep worker-visible market inputs separate from evaluator-only future labels, and record an immutable dataset manifest.

The implementation preserves the full PR-04 checklist scope: dynamic date ranges; authorized warmup and label extensions; Futures and Spot OHLCV; Funding and Open Interest; point-in-time cross-sectional membership; explicit missing-data behavior; immutable provenance; and removal of fixed 2022–2024 assumptions from new interfaces.

## Design brief

- The request and its registered campaign policy are the authority for symbols, timeframes, modes, data types, and the selected discovery, validation, or holdout window.
- The selected campaign split bounds every source read, including warmup and future label observations. The builder never crosses into an adjacent split implicitly.
- Market feature inputs and future labels are separate products. Worker input is obtained through an as-of view that excludes observations unavailable at that decision time.
- Existing `MarketData`, `MarketPanel`, `CrossSectionalDataset`, Catalog v3, and source adapters are reused where their contracts fit; new contracts remain versioned and immutable.
- Missing data is either rejected or preserved as missing, as selected by the validated build configuration. The builder never invents observations or silently fills values.

## Approaches considered

1. **Extend the existing panel CLI and loaders.** This minimizes new code but spreads authorization, temporal planning, PIT joins, and manifest behavior across callers. It does not provide one reusable contract for TS and CS consumers.
2. **Add a request-driven `DatasetBuilderV2` over Catalog v3.** This centralizes policy validation, interval planning, source resolution, availability alignment, missingness, dataset construction, and output fingerprints while reusing existing market data objects. This is the selected approach.
3. **Build a general catalog query engine.** This could serve arbitrary future dataset types, but would duplicate Catalog responsibilities and broaden PR-04 beyond its research dataset purpose.

## Architecture

### Inputs and authorization

`DatasetBuilderV2` receives:

- A parsed `ResearchRequestV2` and the trusted `CampaignPolicyRegistry` used to validate it.
- A Data Catalog v3 root and configured source roots.
- A trusted, versioned availability-rule registry for each requested data type and source identity.
- A build configuration containing the missing-data policy, requested universe definition for CS work, and any approved source snapshot pins.
- An output root for immutable built artifacts and manifests.

The builder revalidates the request against the registered campaign policy at build time. A requested symbol, timeframe, mode, data type, interval, or temporal parameter outside that policy is rejected regardless of missing-data policy. Catalog datasets are matched by their full identity (exchange, market type, asset, exchange symbol, data kind, and timeframe); symbol text alone is never sufficient. Ambiguous, invalid, or unauthorized source identity always fails. If an authorized requested source is absent, `reject` fails while `preserve` represents its values as missing and reports the condition. The primary market panel must exist under either policy to define decision rows.

For reproducibility, each build resolves one unique latest valid Catalog v3 manifest per selected source, then pins its manifest fingerprint. A caller may rebuild from explicit prior pins. Invalid lineage, a changed source object, multiple latest revisions, or source-root verification failure rejects the build.

### Temporal planning

All intervals use UTC and half-open `[start, end)` semantics. Let `step` be the requested timeframe duration, `H` the requested `lookback_bars`, and `L` the requested `label_horizon_bars`.

- **Study decisions:** observations with decision times in the request interval `[start, end)`.
- **Warmup:** the `H` expected timeframe slots immediately before study start, bounded by `selected_split.start`. The initial discovery interval is `[max(selected_split.start, start - H * step), start)`. Missing expected slots do not cause the builder to silently reach farther back; they follow the configured missing-data policy. Warmup rows are available to a worker only for an as-of view at or after their availability time; they are not evaluation rows.
- **Label extension:** source observations needed to compute labels for study decisions, through the first exclusive boundary sufficient to include the bar at offset `L` for the last study decision. This boundary must not exceed the selected split end.
- **Decision boundary:** each row's decision time is explicit. No observation with `available_time > decision_time` appears in that row's worker view.

When the selected split boundary prevents a complete warmup or label horizon, the configured missing-data policy controls the result: `reject` fails with the exact unavailable interval; `preserve` retains the decision row and marks the affected feature or label missing, with a quality issue. Neither behavior reads from another split. Label availability time is the maximum availability time of the source observations used by the label.

The request's `embargo_bars` is applied by a validated temporal partition operation on the built dataset. Training rows are retained only when their labels are available by the train cutoff; test rows begin after the configured embargo offset from that cutoff. The partition operation reports excluded rows and never moves a row across campaign split bounds. The builder does not guess an internal train/test cutoff when none is supplied.

### Source resolution and point-in-time alignment

Catalog v3 remains the immutable source snapshot registry. A source resolver reads only verified manifest partitions and returns canonical observations plus pinned source identity and quality metadata.

- **Futures OHLCV and Spot OHLCV:** timestamps are normalized to UTC. Availability is derived by a registered rule from the bar close and an explicit delay assumption, or from row-level source evidence when available. The rule identity and whether it is evidenced or conservative are recorded; an assumption is never described as a measured publication time.
- **Funding Rate and Open Interest:** event timestamps do not by themselves prove availability. Each event row must have a source-attested availability time or use an explicitly approved conservative availability rule for that exact source identity. Unverified event data is rejected for worker input. Sparse event cadence is not treated as dense OHLCV missing bars.
- **As-of joins:** all auxiliary observations are joined with `available_time <= decision_time`; optional source-specific maximum age is enforced. There is no backward fill, forward fill beyond the approved staleness limit, or join on event time alone. The matched event timestamp, availability time, age, and PIT status remain traceable.
- **Multiple market streams:** Spot and Futures observations are kept as separate typed inputs and aligned to the target decision grid by availability time. The builder does not merge their prices or select one as canonical without an explicit feature contract.

The availability registry is immutable and versioned (`AvailabilityRuleV1`). A source-attested rule may consume a content-addressed, row-keyed `AvailabilityEvidenceV1` sidecar whose source identity and digest are bound to the pinned Catalog manifest. A conservative rule defines a fixed availability delay for an exact source identity and data kind. Any row whose availability cannot be established by validated evidence or a registered conservative rule is marked `UNVERIFIED` and cannot enter worker-visible input. The manifest records the rule version and digest, and evidence-sidecar digest when used, per source.

### Dataset products

The builder creates a `BuiltDatasetV2` with three separately fingerprinted products:

1. **Worker market inputs:** only requested source fields, warmup/study rows, availability metadata, and point-in-time membership. It contains no label values, label end timestamps, or future-only observations. The public API exposes `asof(decision_time)`; the returned view contains only rows and auxiliary observations available by that cutoff. Downstream worker isolation remains PR-05's responsibility.
2. **Evaluator labels:** trusted evaluator-only labels for study decisions, including label value, entry time, label end time, label availability time, and validity reason. Labels are computed only after the source resolver has loaded the bounded label extension. They are not part of the worker serialization contract.
3. **Build report:** source coverage, missing intervals, rejected or preserved rows, temporal bounds, availability decisions, membership counts, and quality status.

For `time_series`, one symbol's `MarketData` is the primary decision grid. For `cross_sectional`, `MarketPanel` is the primary grid and labels align exactly to `(open_time, symbol)`. Existing dataset types may be adapted internally, but the public products preserve the worker/evaluator separation.

### Universe membership

Cross-sectional builds require an explicit, fingerprinted universe definition. Fixed universes are permitted when explicitly requested and carry a survivorship caveat. Historical membership uses only listing/eligibility records effective by each decision time. Dynamic membership uses only observations available before or at the decision time and applies its registered rolling rules without future rows. Missing listing or membership history is reported and follows the configured missing-data policy; current constituents are not retroactively treated as historical constituents.

### Missing-data policy

The build configuration requires exactly one policy:

- **`reject`:** fail when a required primary observation, required warmup, requested source interval, event availability rule, label horizon, or configured coverage threshold is unsatisfied. Return structured issue codes and affected intervals.
- **`preserve`:** keep the primary decision grid and preserve unavailable values as null with masks and quality issues. Do not interpolate, zero-fill, duplicate, or synthesize observations. A wholly missing primary market source still fails because there is no valid decision grid.

Expected event sparsity is governed by event cadence and staleness, not dense-candle completeness. The manifest records the policy and its resulting missingness summary.

### Immutable output and manifest

Each build is stored under a content-addressed output identity. Publication is atomic and does not overwrite prior artifacts. The first manifest contract is `alpha-lab-dataset-manifest-v1`; the worker/evaluator result contract is `BuiltDatasetV2`; build options are versioned independently. Repeating the same request, pinned input manifests, builder version, availability rules/evidence, universe, and missing-data policy yields the same canonical content fingerprints. Volatile creation time is recorded for audit but excluded from content fingerprints.

The versioned Dataset Manifest includes:

- Manifest schema/version, builder version, build fingerprint, and creation metadata (with creation time excluded from the canonical content hash).
- Canonical ResearchRequest JSON/hash and CampaignPolicy identity/hash.
- Selected campaign split and the study, warmup, label-extension, and actual observed intervals.
- Every source Dataset ID, full identity, Catalog manifest hash, selected partition fingerprints, and source quality status.
- Availability rule IDs/versions/digests, evidence status, and conservative assumptions.
- Missing-data policy and issue/coverage summary.
- TS/CS mode, timeframe, symbols, universe definition/hash, and membership fingerprint when applicable.
- Worker-input, evaluator-label, and report fingerprints.

The manifest is a separate research artifact and does not mutate or replace Catalog v3 source manifests. New dataset interfaces are date-range driven and do not depend on fixed 2022–2024 names or limits.

## Failure behavior

Build failures are structured and fail closed. At minimum, distinguish invalid request/policy, unauthorized identity or interval, missing source, invalid Catalog lineage, source hash mismatch, insufficient warmup, label horizon beyond split, unverified availability, stale event observation, invalid membership, and rejected missingness. Preserve mode records recoverable missingness but does not suppress authorization, integrity, schema, or PIT failures.

No source download occurs during build. Data acquisition remains in PR-03's maintainer-only staged workflow. No legacy market source, catalog, research record, or frozen manifest is rewritten.

## Compatibility and migration

- `ResearchRequestV2`, CampaignPolicy v1, and Catalog v3 remain the authorization and source contracts. PR-04 adds versioned `AvailabilityRuleV1`, `AvailabilityEvidenceV1`, and `alpha-lab-dataset-manifest-v1` contracts without changing Catalog v3's manifest schema or breaking its readers.
- Existing `MarketData`, `MarketPanel`, labels, and universe implementations remain usable through adapters where their invariants match this spec.
- The fixed-window `panel_cli` behavior is replaced for new builds by a request-driven interface. Historical five-asset 2022–2024 results remain reproducible through an explicit request and pinned source snapshots, not through a hidden global date constant.
- PR-05 consumes only the worker-input contract. PR-06 consumes evaluator labels and the build manifest. Neither may reconstruct source authorization or infer a different time boundary.

## Acceptance and verification

PR-04 is accepted only when all checklist cases are demonstrated with pinned data and exact command/output evidence:

1. Build BTC 2026 5m and ETH/BTC 2025 1h datasets from authorized requests.
2. Reproduce the historical five-asset 2022–2024 1h panel using an explicit request and pinned snapshots; record any source coverage gaps without filling them.
3. Perturb observations strictly after a decision time and prove the worker input fingerprint and factor input at that time remain unchanged.
4. Test warmup at the selected split start, insufficient warmup, label extension at split end, exact label availability, label horizon, and embargo partition boundaries.
5. Prove an unauthorized symbol, timeframe, data type, source identity, or split cannot enter either output.
6. Test PIT joins for Spot, Futures, Funding, and OI, including late/unverified availability, exact as-of boundary, maximum age, and no implicit fill.
7. Test `reject` and `preserve` behavior for missing candles, sparse events, absent requested datasets, and partial source coverage.
8. Test deterministic manifest/artifact fingerprints, pinned-source rebuilds, tampered-source rejection, and atomic no-overwrite publication.
9. Confirm the generic request-driven CLI accepts legal dates across years and has no global 2022–2024 bound.
10. Run focused unit, Catalog v3 integration, legacy compatibility, PIT, and governance/freeze checks; record actual-data and synthetic evidence separately.

## Scope boundaries

PR-04 builds and publishes authorized datasets. It does not implement Python worker isolation, MCP/Agent interfaces, descriptive evaluation, formal statistical inference, trading, or source downloading. Those remain PR-05 through PR-08 and may consume the contracts defined here only after their own design and acceptance gates.
