# Data Engineering v0.1 — offline-first baseline

Purpose: Data acquisition is **separate** from Alpha Lab computation. This module is maintainer-only and has no exchange credentials or trading. Only the explicitly invoked `fetch-defillama` command performs an allowlisted HTTPS request; Alpha Lab never downloads data. It never changes Freqtrade's existing data/config or Alpha Lab's frozen core.

## Layout

- Existing Freqtrade Feather remains at `user_data/data/binance/`.
- Data stewardship writes immutable content-addressed CSV snapshots under `user_data/data_engineering/snapshots/<dataset_id>/<sha256>.csv` with manifest.
- The Alpha Lab cross-sectional runner consumes validated bounded market CSV under `user_data/research/alpha_lab/panels/`.
- Raw downloaded fundamental exports stay in a maintainer-only staging area **outside the Research Agent workspace**.

## Quick start

From a clean checkout, install the standalone package and its test extra:

```bash
python -m pip install -e 'data_engineering[test]'
python -m data_engineering inventory --datadir user_data/data/binance
python -m pytest -q tests/test_data_engineering.py tests/test_fundamentals.py \
  tests/test_fundamental_asof.py tests/test_fundamental_feature_bridge.py \
  tests/test_pit_evidence.py tests/test_pit_audit.py
```

The inventory displays only file names and sizes, never loads Feather content.

### Market-data onboarding

**Do not feed existing mixed 2022–2026 Feather files into a research importer.** First produce a physically bounded, explicitly verified 2022–2024 market source in a separate Maintainer environment. One option is Freqtrade's independent `download-data --timerange 20220101-20250101` into a separate data directory. Audit that resulting source contains no 2025+ rows *before* using the existing `alpha_lab.panel_cli` in the maintainer context. The operation must not unlock the Research Agent's holdout data.

For a *verified bounded* source, the existing importer can produce a market panel:

```bash
python -m alpha_lab.panel_cli \
  --symbol BTC/USDT:USDT --symbol ETH/USDT:USDT \
  --timeframe 1h --datadir user_data/data/binance_bounded \
  --start 2022-01-01 --end 2025-01-01 \
  --output user_data/research/alpha_lab/panels/usdm_1h_development.csv
```

Publish the bounded CSV, then verify it (use the actual returned digest):

```bash
python -m data_engineering ingest --kind market \
  --dataset-id usdm_1h_dev --timeframe 1h \
  --input user_data/research/alpha_lab/panels/usdm_1h_development.csv \
  --source freqtrade-bounded-feather
python -m data_engineering verify --dataset-id usdm_1h_dev --sha256 <sha256-from-ingest>
```

To create a new verified copy for the Lab without overwriting earlier panels:

```bash
python -m data_engineering export-market --dataset-id usdm_1h_dev \
  --sha256 <sha256-from-ingest> \
  --output user_data/research/alpha_lab/panels/usdm_1h_dev_v1.csv
```

### Fundamental onboarding (exploratory-only)

The Data Maintainer can explicitly retrieve `GET https://api.llama.fi/v2/historicalChainTvl/Ethereum` to a content-addressed raw file **outside the Agent workspace**. The raw response can include locked years, so never give the Research Agent access to the raw directory. The official free endpoint provides historical TVL but does not prove the time each historical value was actually published. The adapter filters normalized observations to 2022–2024 and explicitly leaves `published_at` empty.

```bash
python -m data_engineering fetch-defillama --chain Ethereum
# Use the exact 'raw' path printed by fetch-defillama below.
python -m data_engineering normalize-defillama \
  --chain Ethereum --input user_data/data_engineering/raw/defillama/Ethereum/<raw-sha256>.json \
  --output /path/to/steward-only/ethereum-tvl-2022-2024.csv
python -m data_engineering ingest --kind fundamental \
  --dataset-id ethereum_chain_tvl_dev --source defillama-chain-tvl \
  --input /path/to/steward-only/ethereum-tvl-2022-2024.csv
```

The manifest marks this data `research_safe=false`. **Do not merge it into strict causal experiments or label it point-in-time verified.** Later introduce independently audited vendor publication metadata and an explicit feature availability rule before using such data in normal research.

### PIT evidence governance

Catalog `ingested_at` records when the local snapshot was published. It is not a source `available_at`. Source observations and any PIT claim are kept separate: a content addressed `pit_attestations/<sha256>.json` v2 sidecar binds each row to the Catalog dataset and snapshot hash. Evidence blobs live under `pit_evidence/<sha256>` and must match their SHA-256. Each evidence record must bind the source asset, metric, event time, exact snapshot value, revision, and claimed availability time; an evidence URI without those matching claims is insufficient. Row records distinguish `event_time`, `available_at` (exact), `available_at_upper_bound` (conservative), `revision_id`, and one of `VERIFIED`, `CONSERVATIVE`, `UNVERIFIED`, or `REJECTED`.

`VERIFIED` needs row-level evidence that binds the source revision and the actual snapshot value to the exact availability time. `CONSERVATIVE` needs equivalent value/revision evidence and an audited upper bound; joins use that bound, never an estimate inferred by the adapter. Legacy PIT v1 sidecars do not establish value identity and cannot authorize strict reads. `UNVERIFIED` and `REJECTED` rows cannot claim availability. Strict reads fail closed unless every selected row is evidence-backed as `VERIFIED` or `CONSERVATIVE`. A vendor `published_at`, event date, fetch receipt, or fixed delay alone does not qualify.

Generate the source eligibility checklist with:

```bash
python -m data_engineering --root user_data/data_engineering pit-audit --format markdown
```

The report in `docs/data_engineering_pit_audit.md` records all 14 current fundamental snapshots and their 30 metric series. The audit opens only a catalogued development snapshot when a sidecar claims to bind it, in order to validate every record and its evidence references. Current snapshots contain no historical source vintages or row-level release evidence, so all remain `UNVERIFIED` and strict research eligibility is zero.

## Boundaries and open acceptance gates

1. First release uses only bounded market CSV exports and explicit DefiLlama chain-TVL HTTPS fetch; it does not download, rewrite, or trust existing mixed-year Feather files.
2. Catalog is append-only in normal operation, content hashed and checked on every read. Do not grant the Research Agent write access to these paths.
3. Normalized fundamental historical values are not PIT-certified. Asset mappings distinguish tokens, chains and ecosystems; this mapping does not turn ecosystem observations into token observations.
4. CSV snapshots and manifests are protected against accidental overwrite but are not a substitute for OS permissions, offline backup, or adversarial process isolation.
5. No continuous downloader, per-source incremental ETL, data-vintage database, token supply, protocol revenue, Freqtrade execution adapter, or real 5+ asset smoke is claimed in v0.1.
6. Existing Alpha Lab v1.0 and v1.1 remain unchanged; this does not lift their current freeze/acceptance gates.
