# Fundamental Data Adapter

## Construction report

Implemented on branch `codex/fundamental-data-adapter`. The sole maintained
runtime package is `data_engineering/`; `data_engineering_starter/` is a retired
handoff archive and its installer refuses to copy duplicate source. The adapter
provides versioned registries, read-only Catalog access, value-bound PIT v2
evidence, as-of joins, coverage preflight, and the Alpha Lab feature bridge.

Changed files include `data_engineering/pit.py`, `pit_audit.py`,
`local_acceptance.py`, `coverage.py`, `fundamentals.py`, and
`alpha_lab_features.py`, plus versioned schemas and tests. Existing Catalog,
DefiLlama, registry and coverage modules are now part of the standalone package.
No Catalog snapshots, raw files, SQLite records, or Freqtrade market data were
changed.

Verification completed:

- Data Engineering unit and integration suite: 68 passed.
- Clean-checkout package install and Data Engineering suite: 68 passed.
- `compileall` and `git diff --check`: passed.
- Read-only local catalog acceptance: 14/14 snapshot hashes verified, 30 metric
  series, 31,980 rows read exploratorily, and 14/14 strict reads rejected.

No current snapshot has evidence proving both its exact historical value and
its source revision and availability time. All 14 remain `UNVERIFIED` and no
fundamental feature can enter strict Alpha Lab evaluation. Source-level PIT
proof remains a data governance blocker.

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
read. Its normalized frame includes `asset_id`, `tradable_asset_id`,
`entity_scope`, `metric`, `event_time`, `available_at`,
`available_at_upper_bound`, effective `available_time`, `ingested_at`,
`revision_id`, evidence hashes, `value`, source and snapshot identity, and
`pit_status`. Frame metadata records registry versions, snapshot pins and
access mode.

Allowed event range is `[2022-01-01, 2025-01-01)` UTC. Start and end filters
must themselves stay inside this interval. The adapter scans only
`snapshots/<dataset_id>/<sha256>.csv`; it never reads `raw/`, `processed/`, or
staging directories. Known availability times must also be inside the
development interval. Catalog and snapshot paths may not contain symlinks.

## Access modes and PIT attestation

`exploratory` reads return observations with their PIT status. An ordinary
source timestamp remains a source declaration and is not copied into effective
`available_time`; without qualifying evidence, effective availability is null.
Event time, ingestion time and fixed delays are never used to fabricate
availability.

`strict_pit` accepts only PIT sidecar v2 at
`pit_attestations/<sidecar_sha256>.json`. Each `VERIFIED` or `CONSERVATIVE`
row must reference hashed evidence binding the Catalog identity, source asset,
metric, event time, exact value, revision, source reference and exact release
time or audited upper bound. A CONSERVATIVE join uses the upper bound. Legacy
v1 attestations lack value identity and cannot authorize strict access. Schema,
row coverage, evidence hashes, snapshot identity and temporal boundaries are
checked fail-closed.

No current snapshot has a qualifying PIT v2 attestation. Therefore all 14
current fundamental datasets are rejected by `strict_pit`; adding a timestamp
column without historical value and revision evidence does not change this.

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
When no observation is joined, the bridge returns an all-null Series marked
`NOT_EVALUABLE`; joined PIT status is preserved (`VERIFIED`, `CONSERVATIVE`, or
`MIXED`). No feature is zero-filled, and no value is carried before its first
effective availability.

`CrossSectionalDataset.frame()` uses complete cases across its selected
features and target, so a row missing any feature is dropped. The optional
`preflight_feature_coverage()` reports per-feature coverage and effective valid
asset counts at each time using caller-supplied minimums. It does not fill data,
alter the Dataset, or modify frozen evaluation/statistical methods.

This bridge feeds the existing `CrossSectionalDataset` input contract. It does
not modify the frozen runner, statistics, inference, evaluation, or Research
Decision behavior. Because current Catalog fundamentals are PIT-unverified,
they can be inspected exploratorily but cannot yet be fed through this strict
feature bridge.

## Current coverage and limitations

The real local acceptance command is
`python -m data_engineering.scripts.validate_local_catalog`. It verified all
14 snapshot content hashes and 30 metric series, read 31,980 rows in
exploratory mode, and confirmed 14 strict-PIT rejections. SOL and BNB have no
Coin Metrics `AdrActCnt` series. XRP ecosystem TVL begins on 2024-03-23, and
Solana Fees and Revenue each lack the first 44 days of 2022. These missing
observations remain absent; coverage does not prove historical availability.
