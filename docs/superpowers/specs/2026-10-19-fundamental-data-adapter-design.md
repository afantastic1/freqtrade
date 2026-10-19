# Fundamental Data Adapter — Design

## Goal and constraints

Add a read-only bridge from the installed Data Engineering v0.1 snapshot Catalog
to Alpha Lab feature construction. Keep all 14 existing snapshots and the
existing SQLite research history unchanged. Do not download, duplicate, rewrite,
or republish raw data. Keep Freqtrade OHLCV, funding, and open-interest paths
unchanged. Do not modify Alpha Lab statistics, inference, evaluation,
Research Decision, or frozen method versions.

The approved research window is `[2022-01-01, 2025-01-01)` UTC. Ordinary
research APIs must reject observations outside this interval. Current history is
PIT-unverified, so it is available for explicitly exploratory reads only.

## Registries and entity semantics

Add a versioned Asset Registry and Metric Registry under `data_engineering`.
Every asset registry entry declares a canonical entity ID, entity scope
(`token`, `chain`, `ecosystem`, or `protocol`), source aliases, and an optional
research tradable asset mapping. `asset_id` in adapter output names the
canonical economic entity; `tradable_asset_id` is a separate relationship.
Mapping an ecosystem or chain metric to a tradable token never changes its
economic scope.

The initial tradable assets are BTC, ETH, SOL, BNB, and XRP. Registry mappings
cover current Coin Metrics token IDs and DefiLlama chain IDs for Bitcoin,
Ethereum, Solana, BSC, and XRPL. Metrics declare allowed entity scopes, sources,
units, and definitions. Protocol-scoped entities are supported as registry
types, but no protocol data is inferred from the current 30 series.
Unknown aliases, metrics, or invalid source/scope combinations fail closed.

## Read-only adapter API

Add `FundamentalDataAdapter` in `data_engineering/fundamentals.py` (or a small
package with the same public boundary). It discovers catalog manifests,
requires explicit `dataset_id` and `snapshot_sha256` pins for reads, calls the
existing Catalog verifier, then reads the immutable CSV snapshot directly.
It does not call a downloader or write to the catalog. Reject symlinked catalog
paths, invalid manifest identity, checksum/schema mismatch, wrong data kind,
unknown registry entries, duplicate observations, and out-of-window data.

The normalized frame contains at least:

`asset_id`, `tradable_asset_id`, `entity_scope`, `metric`, `event_time`,
`available_time`, `value`, `source`, `dataset_id`, `snapshot_sha256`,
`pit_status`.

`available_time` remains null when the source has no actual publication or
availability time. Neither `event_time` nor a fixed lag may be substituted.
Source-provided publication times are not automatically PIT-certified. Only an
explicitly audited manifest status of `verified`, paired with a real
availability-time column, is eligible for strict PIT reads. Because the v0.1
Catalog manifests are immutable, PIT certification is recorded in an
append-only content-addressed sidecar at `pit_attestations/<sha256>.json`; it
binds the dataset ID, snapshot SHA, reviewer, audit reference, status, and
availability column without rewriting the manifest.

`exploratory` reads may return unverified observations and their metadata.
`strict_pit` reads reject any unverified or missing availability time. No write
or database API is added.

## Point-in-time join and Alpha Lab bridge

Expose a strict as-of feature join against `MarketPanel` decision times. The
join keys include the explicitly registered tradable asset mapping and metric;
only a verified `available_time <= decision_time` row can match. Callers must
specify a maximum observation age. An unmatched value stays null. No
event-time-based as-of join is exposed for unverified data.

Add an adapter-owned `read_many` method for multiple explicitly pinned
snapshots. It verifies every snapshot and returns a combined frame whose
content fingerprint and attestation set are checked again before a join.
Add a separate Data Engineering bridge that returns a feature mapping aligned
to the existing `MarketPanel` index and accepted by
`CrossSectionalDataset(..., features=...)`. It does not patch the frozen Alpha
Lab runner/evaluator. Feature names include entity scope and metric to prevent
token/ecosystem collisions. Missing SOL/BNB series and XRP pre-listing TVL
remain null; the bridge never fills zero or carries values before a first
observation. Any carry-forward after a verified observation is bounded by the
required maximum age and remains an as-of lookup, not synthetic data creation.

## Versioning and access boundaries

Snapshot dataset IDs and SHA-256 digests are part of adapter output and join
identity. Registry versions and requested access mode are exposed in metadata.
The adapter accepts a catalog root, defaults to the existing
`user_data/data_engineering` Catalog, and never falls back to raw, processed,
or staging directories. It rejects date ranges beyond the development window.
Existing Alpha Lab databases and Catalog files are read-only inputs.

## Testing

Use temporary synthetic catalogs for unit tests. Cover entity mapping and
scope separation, unknown mappings, exploratory versus strict access, unknown
publication time preservation, future/out-of-window rejection, as-of leakage,
maximum age, missing metrics and XRP pre-listing nulls, pinned digest/version
selection, checksum and manifest tampering, and symlink/path rejection.
End-to-end tests publish temporary fundamental snapshots using the existing
Catalog API, add content-addressed attestation sidecars, read multiple pins via
the adapter, and align features to a synthetic MarketPanel without changing
existing market loaders or SQLite.

## Known data limitations

The read-only coverage audit observed 14 manifests, 30 series, and no catalog
errors. All series are currently `unverified` for PIT. SOL/BNB have incomplete
metric menus, and XRP chain TVL begins on 2024-03-23. The strict adapter must
therefore reject the current fundamental snapshots; successful exploratory
reads do not establish causal feature availability.
