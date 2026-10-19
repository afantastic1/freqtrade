# Fundamental Data Adapter

## Construction report

Implemented on branch `codex/fundamental-data-adapter`. The adapter adds
versioned economic entity and metric registries, pinned read-only snapshot
access, attestation-gated strict PIT reads, an as-of join, and an Alpha Lab
feature bridge. The coverage audit now consumes those same registries, so its
entity labels cannot drift from feature normalization.

Changed implementation files are `data_engineering/registries.py`,
`data_engineering/fundamentals.py`, `data_engineering/alpha_lab_features.py`,
and `data_engineering/scripts/fundamental_coverage_audit.py`. Regression tests
cover the adapter, registry scopes, as-of availability, the Alpha Lab handoff,
and catalog coverage audit. Existing v0.1 Catalog source files were already
present as untracked workspace assets and remain outside the feature commit;
this branch expects that installed package to be present. No Catalog snapshots,
raw files, SQLite records, or Freqtrade market data were changed.

Verification completed:

- Fundamental adapter/catalog integration tests: 56 passed.
- Frozen Alpha Lab suite: 297 passed.
- `compileall` and `git diff --check`: passed.
- Read-only snapshot audit: 14/14 manifests, 30 metric series, 0 errors;
  all PIT statuses remain unverified.

There are no verified publication-time attestations in the current Catalog, so
strict PIT joins correctly reject all current fundamental snapshots. No
fundamental feature can yet enter a strict Alpha Lab evaluation until a data
steward audits and attests its historical availability. The untracked v0.1
package must be committed or installed separately before this feature commit
can be replayed in a clean checkout.

The adapter reads immutable Data Engineering v0.1 snapshots from the existing
Catalog. It does not download, publish, copy, or modify source data. It does not
touch SQLite research history or the Freqtrade OHLCV, funding, or open-interest
loaders.

## Economic identities

The registries are versioned in `data_engineering.registries`:

| Source identity / metric | Canonical `asset_id` | Scope | Tradable relation |
|---|---|---|---|
| Coin Metrics `btc` / `SplyCur`, market cap, MVRV | `token:BTC` | token | BTC |
| Coin Metrics `btc` / `AdrActCnt`, `TxCnt`, `HashRate` | `chain:Bitcoin` | chain | BTC |
| DefiLlama `chain:Bitcoin` / chain TVL | `ecosystem:Bitcoin` | ecosystem | BTC |
| DefiLlama `chain:Solana` / fees and revenue | `ecosystem:Solana` | ecosystem | SOL |
| DefiLlama `chain:BSC` / fees and revenue | `ecosystem:BSC` | ecosystem | BNB |

The same separation applies to ETH/Ethereum and XRP/XRPL. A chain or ecosystem
metric keeps its own `asset_id`; `tradable_asset_id` is only a registered
relationship to the corresponding USDⓈ-M market. `chain` and `protocol` are
supported scopes, but no current Catalog series is registered as protocol data.
Unknown aliases, metric/source combinations, and scope mismatches fail closed.

## Pinned read

Every read pins both dataset ID and content digest. `list_snapshots()` validates
Catalog manifests and returns available fundamental snapshot references.

```python
from data_engineering.fundamentals import FundamentalDataAdapter

adapter = FundamentalDataAdapter("user_data/data_engineering")
refs = adapter.list_snapshots()
snapshot = next(ref for ref in refs if ref.dataset_id == "coinmetrics_btc_dev_v1")

observations = adapter.read(
    snapshot.dataset_id,
    snapshot.snapshot_sha256,
    access="exploratory",
)
```

For a multi-asset universe, pin every required snapshot and let the adapter
validate and combine them. Do not concatenate strict frames directly;
`read_many` carries the verified pins and attestation set and binds the
normalized rows to a content fingerprint that `asof_join` checks again.

```python
observations = adapter.read_many(
    [(ref.dataset_id, ref.snapshot_sha256) for ref in refs],
    access="strict_pit",
)
```

The adapter verifies the snapshot with the existing Catalog verifier on each
read. Its normalized frame contains `asset_id`, `tradable_asset_id`,
`entity_scope`, `metric`, `event_time`, `available_time`, `value`, `source`,
`dataset_id`, `snapshot_sha256`, and `pit_status`. Frame metadata also records
asset registry version, metric registry version, and access mode.

Allowed event range is `[2022-01-01, 2025-01-01)` UTC. Start and end filters
must themselves stay inside this interval. The adapter scans only
`snapshots/<dataset_id>/<sha256>.csv`; it never reads `raw/`, `processed/`, or
staging directories. Known availability times must also be inside the
development interval. Catalog and snapshot paths may not contain symlinks.

## Access modes and PIT attestation

`exploratory` reads return observations even when PIT status is
`unverified` or `declared_not_audited`. Actual source `available_time` or
`published_at` values are preserved when present; an absent timestamp remains
null. Event time and fixed delays are never used to fabricate availability.

`strict_pit` requires all rows in the pinned snapshot to have actual
availability timestamps and an append-only, content-addressed attestation
sidecar at `pit_attestations/<evidence_sha256>.json`. The sidecar must bind the
dataset ID, snapshot SHA, `status: verified`, reviewer, audit reference, and
timestamp column (one of `available_time`, `published_at`, or
`available_time_or_published_at`). Its content hash is the filename; snapshot
manifests remain unchanged. When both time columns are present, non-null values
must agree, and a null `available_time` may use the actual `published_at`
value. A source-provided timestamp alone is not an audit.

No current snapshot has a PIT attestation. Therefore all current fundamental
datasets are rejected by `strict_pit`; adding a timestamp column without an
independent audit does not change this.

## Strict as-of feature handoff

Only normalized observations returned by `read(..., access="strict_pit")` can
enter `asof_join`; merely setting a DataFrame status or `attrs` field is not
accepted. It maps market
symbols through the Asset Registry and matches backward on
`available_time <= MarketPanel.frame.available_time`. The caller must choose a
maximum age; expired and unmatched rows remain null. It rejects observations
or market decisions outside the development interval. The feature bridge
returns Series aligned exactly to the `(open_time, symbol)` panel index:

```python
from data_engineering.alpha_lab_features import build_fundamental_features

features = build_fundamental_features(
    panel,
    verified_observations,
    metrics=["CapMrktEstUSD", "chain_tvl_usd"],
    max_age="48h",
)
dataset = CrossSectionalDataset(panel, membership, features, labels)
```

Feature names include the economic scope, such as
`fundamental__token__CapMrktEstUSD` and
`fundamental__ecosystem__chain_tvl_usd`. Series metadata carries registry
versions, dataset IDs, snapshot hashes, sources, maximum age, and PIT status.
When a registered metric has no observations, the bridge returns an all-null
Series marked `not_evaluable`. No feature is zero-filled, and no value is
carried before its first available observation.

This bridge feeds the existing `CrossSectionalDataset` input contract. It does
not modify the frozen runner, statistics, inference, evaluation, or Research
Decision behavior. Because current Catalog fundamentals are PIT-unverified,
they can be inspected exploratorily but cannot yet be fed through this strict
feature bridge.

## Current coverage and limitations

The read-only Catalog audit found 14 of 14 expected manifests, 30 metric
series, and zero errors. All remain PIT-unverified. SOL and BNB have fewer
Coin Metrics series than BTC/ETH/XRP. XRP ecosystem TVL begins on
2024-03-23; earlier XRP values remain null. Coverage does not establish that
an observation was available at its event time.
