# Data Catalog v3 and Coverage Query Design

**Status:** Draft for review
**Roadmap item:** PR-02, Data Catalog v3 and coverage query
**Date:** 2026-10-20

## Intent and scope

Add a versioned, append-only catalog for dynamic market datasets that reports actual coverage and data quality. The first real-data path must catalog existing local Freqtrade Feather files without modifying them and must report BTC futures 5m coverage in 2025–2026. Missing historical Funding Rate or Open Interest observations remain missing; the catalog never synthesizes zero-valued observations.

This change adds catalog metadata, validation, local read-only discovery, coverage queries, and a CLI. It does not download data or modify source files. Data-source downloads and normalized Parquet publishing remain PR-03 work. Dataset and panel construction remain PR-04 work. Formal statistical inference remains blocked as stated in the roadmap.

## Considered approaches

1. **Add a separate v3 catalog alongside v1/v2 (recommended).** Keep the current manifest schemas, `validate_manifest`, publisher, and reader intact. Add an independent v3 implementation that can represent market identity, distinct data kinds, temporal coverage, and revision lineage. This limits compatibility risk and supports migration at the catalog boundary.
2. **Expand the current catalog in place.** Fewer top-level modules, but the existing publisher and market validator encode the 2022–2024 development window and only accept market/fundamental CSV. Relaxing them risks changing old behavior and old snapshot validation.
3. **Replace the catalog with a single generic data lake registry.** This could accommodate future formats, but is broader than PR-02 and would entangle PR-03 adapters and PR-04 dataset building with this delivery.

The additive design is selected. Version dispatch is explicit; a v1/v2 document is always validated with its original schema and logic, while v3 manifests are handled only by the v3 API.

## Data model

### Dataset identity

A v3 manifest identifies a dataset by exchange, market type, canonical asset, data kind, and sampling interval. `market_type` is `spot` or `futures`; `data_kind` is a registered contract such as `ohlcv`, `funding_rate`, `open_interest`, or `mark_price`. This keeps Spot/Futures as market scope rather than conflating Spot with a column-level data kind. Exchange-native symbols are stored separately from canonical asset identifiers.

The manifest contains a stable dataset ID, schema version, identity fields, source and source reference, registration time, quality state, ordered temporal partition records, and revision lineage. A partition record contains its half-open UTC interval, configured source-root ID plus source-relative path, observed first/last timestamps, row count, source SHA-256, deterministic content fingerprint, source/provenance metadata, quality state, and validation summary. Catalog manifests live under the catalog root; source roots are separately configured and paths cannot escape their selected root. Symlinks are rejected.

Partition intervals use UTC and `[start, end)` semantics, matching ResearchRequest v2. Empty time intervals are not represented as observations. A source file may span multiple calendar partitions; each partition fingerprint is computed deterministically from the contract-normalized observations in that interval, while the source SHA-256 binds the record to the immutable source bytes. This permits monthly coverage/version queries over existing monolithic Feather files without rewriting them.

### Data-kind contracts

- `ohlcv`: timestamp, open, high, low, close, volume. Prices must be finite and positive; volume must be finite and nonnegative; low/high must bound open/close and each other.
- `funding_rate`: observation timestamp and finite funding rate, with optional explicit exchange settlement interval metadata. No interpolation or forward fill is implied.
- `open_interest`: observation timestamp and at least one finite, nonnegative amount or value measure. The source frequency is retained; sparse observations are not expanded to candle frequency.
- `mark_price`: timestamp and finite positive mark price. Spot is represented by `market_type=spot` with the relevant supported contract, not as a separate `data_kind`.

Unknown data kinds and extra contract-required omissions fail validation. The registry is explicit and versioned so future contracts do not silently reinterpret existing manifests.

## APIs and status behavior

The v3 module exposes:

- `list_datasets(root, filters...)`: returns registered/discovered datasets with deterministic ordering and identity/coverage summaries. Optional filters include exchange, market type, asset, kind, and timeframe.
- `get_coverage(root, dataset_id, start, end)`: returns requested half-open interval, observed intervals, expected timestamps where cadence is defined, missing intervals, partition versions/fingerprints, and an aggregate status.
- `validate_dataset(root, dataset_id)`: validates manifest schema, source identity, source and partition hashes, contract fields, timestamp uniqueness/order/alignment, value constraints, and recomputed coverage/fingerprints. It returns `INVALID` with actionable issues for malformed or changed data.

Coverage status is defined against the requested interval:

- `AVAILABLE`: valid observations cover the entire requested interval at the declared cadence, with no detected gaps.
- `PARTIAL`: at least one valid observation intersects the interval, but the requested interval is not fully covered or contains gaps.
- `MISSING`: no registered observations intersect the requested interval, or the dataset ID is unknown.
- `INVALID`: a manifest, source, hash, or observation violates its declared contract. Invalidity takes precedence over coverage.

For event-driven or sparse kinds such as Funding Rate and Open Interest, coverage reports observed intervals and source frequency without inventing an expected dense cadence. The caller may request an interval, but the catalog does not treat absent event rows as zero or as proof of uninterrupted availability. For OHLCV, cadence is explicit and missing candles are enumerated as ranges (with a configurable output cap and total count to keep reports bounded).

Validation detects duplicate timestamps within the contract's identity key, malformed or misaligned candle timestamps, OHLCV anomalies, source changes, and missing/extra rows at known cadence. Records with no source observations return `MISSING`, never a synthetic successful dataset.

## Discovery, registration, and immutable versions

The initial local source is the existing Freqtrade data directory, including futures Feather files whose filenames encode symbol, timeframe, and data kind. Read-only discovery maps the exchange/scope from the configured source root and parsed filename, then inspects Feather columns and timestamps against the registered contract. Discovery does not download, rewrite, normalize, or overwrite source data. Unsupported or ambiguous files are surfaced as invalid/unregistered entries rather than guessed into a dataset.

Catalog registration writes manifests atomically under the v3 catalog root. Re-registering identical source bytes and identity yields the same deterministic dataset/partition fingerprints. A changed source creates a new immutable version; it does not overwrite prior partition records. A manifest revision names the prior version it supersedes and records the reason/source metadata when available. Content fingerprints exclude ingestion/registration timestamps so repeat registration is deterministic.

The v3 CLI provides read-only `list`, `coverage`, and `validate` operations plus a maintainer-only local registration/discovery operation. No network action is added to catalog queries. Existing legacy CLI commands retain their behavior.

## Compatibility and safety invariants

- Do not edit the v1 or v2 JSON Schemas or reinterpret old manifests.
- Preserve legacy validation and read paths; add explicit v3 dispatch.
- Never fill absent OHLCV, Funding, Open Interest, or Spot history with zeroes.
- Never claim coverage outside the observed timestamps or verified source partitions.
- Reject symlink traversal and source paths outside the configured catalog/data roots.
- Keep catalog registration separate from downloading and trading.
- Use append-only revision records; never replace a prior version in place.
- Keep output deterministic apart from explicitly recorded registration timestamps.

## CLI and deliverables

The implementation will add the v3 schema, contract registry, validator, catalog store/query API, coverage report, local Feather discovery/registration path, and CLI subcommands. The existing `data-engineering` entry point remains the only executable boundary; v1/v2 commands continue to work.

## Acceptance evidence

Implementation is accepted when:

1. Querying local BTC futures 5m for `[2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)` and a second query extending through the latest available 2026 timestamp reports the observed range, partitions, hashes, and any gaps from the actual Feather source.
2. Queries return gaps and immutable version/revision metadata, with deterministic content fingerprints for identical source content.
3. OHLCV, Funding Rate, Open Interest, and Spot/Futures identity contracts validate their own required fields and quality rules.
4. Duplicate timestamps, timestamp misalignment, missing candles, invalid prices, and tampered source data produce the specified diagnostics/statuses.
5. Unknown datasets and empty intervals report `MISSING`; sparse OI/Funding remain sparse and no zero rows are produced.
6. Existing v1/v2 fixtures and stored manifests still validate and read without schema changes.
7. CLI list/coverage/validate behavior and tests cover actual local data separately from synthetic contract fixtures.

## Risks and mitigations

- **Filename ambiguity:** Freqtrade file names do not always encode every exchange/market attribute. Require an explicit source-root configuration and refuse ambiguous mappings.
- **Large files:** Full hashing and validation of Feather files can be expensive. Stream file hashing; bound report size; cache only by verified size/mtime plus digest policy, never use cache as the integrity authority.
- **Sparse-event coverage semantics:** A dense coverage percentage would mislead for OI/Funding. Report observations and observed-time bounds; do not synthesize cadence or fill values.
- **Mutable source files:** A file can change after discovery. Bind each registration to source SHA-256 and re-verify on validation/query when integrity is required; report `INVALID` on mismatch.
- **Timezone/cadence assumptions:** Require explicit UTC timestamps and declared cadence for dense contracts. Reject naive timestamps and avoid inferring cadence from filenames alone when the contract is ambiguous.
