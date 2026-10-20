# Data Source Adapters and Incremental Preparation Design

**Status:** Draft for review
**Roadmap item:** PR-03, Data source adapter and incremental preparation
**Depends on:** PR-01 ResearchRequest v2 and CampaignPolicy v1; PR-02 Data Catalog v3
**Date:** 2026-10-20

## Intent

Add a controlled path from approved local or public historical sources into immutable, normalized monthly Parquet partitions. The path must discover what exists, calculate only missing work, download only when requested, validate before publication, and record enough provenance to reproduce each partition. Existing Freqtrade Feather files and downloaded Binance Vision archives remain unchanged.

The first external source is the existing Binance Vision monthly USD-M Futures kline archive flow in `scripts/binance_vision_tradfi.py`. The first local sources are Freqtrade Feather histories. The feature stays in the local data engineering CLI; it does not expose download or publish operations through MCP or Agent tools.

## Scope

### Included in PR-03

- One typed `DataSourceAdapter` contract for source inspection and normalized observations, with source-specific discovery and acquisition capabilities.
- A Freqtrade local adapter for existing Futures and Spot OHLCV, Funding Rate, Open Interest, and supported mark-price Feather files. It reuses Freqtrade’s native Feather handler where needed, with cleaning/fill behavior disabled so validation sees the original rows.
- A Binance Vision adapter that reuses the existing monthly USD-M Futures OHLCV importer and checksum rules. It adds a separate, explicitly reviewed crypto allowlist for BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, and XRPUSDT; it does not mutate the TradFi catalog. The optional 1m BTC/ETH validation experiment is included only as an explicit opt-in, not as a bulk default.
- Four separately callable stages: `discover`, `download`, `validate`, and `publish`.
- A dry-run plan that compares a requested dataset/date interval with verified catalog coverage and reports missing monthly artifacts, known unavailable months, and planned work without fetching archive payloads or writing/publishing files. Vision discovery may make bounded metadata-only listing or HEAD requests; local discovery is offline.
- Incremental retrieval of missing complete calendar months only. Existing valid partitions are skipped. A partial month, if explicitly allowed by the caller, is represented as a separate bounded interval and never mislabeled as a complete month.
- Conversion of validated observations into immutable, time-partitioned Parquet, with provenance and catalog v3 manifests.
- Explicit status distinctions for source-month absence, transport/download failure, checksum failure, parse/quality failure, and already-published content.
- CLI, regression fixtures, and one small real-source acceptance case when the archive is available.

### First source and dataset matrix

| Adapter | Data kinds | Market scope | Initial behavior |
|---|---|---|---|
| Freqtrade local Feather | OHLCV, Funding Rate, Open Interest, mark price | Spot and Futures, based on explicit caller scope | Discover/read existing files; validate and optionally normalize to Parquet; never download or change the source |
| Binance Vision monthly archives | OHLCV | USD-M Futures only | Discover reviewed symbol/month/timeframe archives; download checksum-verified archives; validate, normalize, and publish |

The initial supported research symbols and intervals are the roadmap’s reviewed BTC, ETH, SOL, BNB, and XRP Futures OHLCV at 5m, 15m, 1h, and 4h. Spot OHLCV at 5m, 15m, and 1h is sourced from existing local Feather files. Funding and Open Interest are sourced only from trusted local histories for which files and observations exist; PR-03 does not synthesize them or add an unreviewed public endpoint. One-minute Vision data is limited to a separate BTC/ETH validation experiment and is not enabled as a bulk catalog default.

## Out of scope

- Dataset or panel construction, PIT joins, cross-sectional membership, worker access, factor execution, and evaluations (PR-04 onward).
- Binance trading endpoints, order placement, private credentials, and live market subscriptions.
- Automatic additions to the reviewed Vision symbol catalog, all-exchange discovery, or scraping arbitrary archive listings into trusted instruments.
- A server-side identity or role-management system. PR-03 is a local operator workflow, not an authentication service.
- Full L2 order-book history, tick-level ingestion, and 1m expansion across the symbol universe.
- Rewriting, deleting, or migrating raw Feather files or previously downloaded ZIP archives.

## Architecture

### Shared adapter contract

The shared contract owns common identity, requested intervals, discovery results, normalized observation schemas, validation reports, and stable source/provenance fields. Source adapters implement capabilities explicitly rather than pretending every source can download:

```python
class DataSourceAdapter(Protocol):
    name: str
    capabilities: frozenset[str]

    def discover(self, request: DatasetRequest) -> list[SourceArtifact]: ...
    def read(self, artifact: SourceArtifact) -> ObservationBatch: ...
    def download(self, artifact: SourceArtifact, destination: Path) -> DownloadResult: ...
```

The local Freqtrade adapter does not implement network retrieval and returns an unsupported-capability result for `download`. The Vision adapter implements discovery and download and then yields observations to the shared validator/converter. The pipeline orchestrator calls only capabilities declared by the selected adapter.

The shared value objects have these required fields:

- `DatasetIdentity`: stable provider/exchange, market type, exchange-native symbol, data kind, interval label, and schema/mapping version. Requested dates are not part of dataset identity.
- `DatasetRequest`: a `DatasetIdentity`, aware UTC `[start, end)` bounds, and a stable request ID. Requests and batch journals may cover one or more intervals without changing dataset identity.
- `SourceArtifact`: dataset identity, request identity, source locator, source format, covered month/bounds, availability state, and optional expected checksum.
- `ObservationBatch`: canonical identity, unmodified observation ordering, source artifact reference, and source/provenance metadata.
- `DownloadResult`: one of `DOWNLOADED`, `ALREADY_CACHED`, `UNAVAILABLE`, `DOWNLOAD_FAILED`, or `CHECKSUM_FAILED`, plus local content-addressed path and issue details when applicable.

`discover` returns metadata and an availability state without returning payload bytes. `read` parses a local artifact into an `ObservationBatch`; it may report parse failure but does not repair it. `download` is available only when declared in `capabilities`.

Dataset IDs retain the PR-02 deterministic identity for the same provider, market type, exchange-native symbol, data kind, and interval; date bounds, request IDs, and source locations are excluded. Asset aliases (for example an exchange-native symbol settling in USDC versus USDT) are explicit mappings and remain distinct dataset identities. Schema or mapping changes use a new versioned identity.

### Stage boundaries

1. **Discover**: enumerate source artifacts and availability metadata for a requested identity and month range. No archive payload bytes are fetched and no normalized data is written. Vision uses the bounded public S3 listing endpoint (`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision`) for metadata and the fixed `https://data.binance.vision` archive host for ZIP/checksum payloads; redirects must remain on the respective allowlisted host. Listing is not evidence that an archive is valid.
2. **Plan / dry-run**: compare discovered artifacts with verified v3 monthly partitions. Return present, missing, unavailable, and already-invalid periods. Dry-run writes nothing and never fetches an archive payload.
3. **Download**: fetch only missing available artifacts from the fixed Binance Vision host, apply bounded retries/timeouts/size limits, preserve the raw ZIP in a content-addressed cache, and verify its official checksum before parsing. A confirmed absent ZIP is `UNAVAILABLE`; a ZIP listed or fetched without its checksum sidecar is `CHECKSUM_FAILED`, as are malformed or mismatching checksums. Transport and size errors are explicit failures. This stage does not publish Parquet.
4. **Validate**: parse the source into canonical observations without filling, sorting away source-order evidence, aggregating duplicate timestamps, or inventing missing values. Check checksum, schema, symbol/month/time bounds, timestamp timezone and cadence, duplicates, numeric finiteness, OHLCV constraints, and expected coverage. Invalid input never reaches publication.
5. **Publish**: write a deterministic monthly Parquet artifact under a staging filename, flush and validate it, then atomically create the immutable final path. Register a new append-only catalog v3 manifest only after the artifact is complete. The object key includes canonical content fingerprint and provenance identity (raw source digest, adapter/converter version, and schema version); identical rows from distinct source bytes remain distinct lineage objects. A repeat with the same complete identity is `ALREADY_PRESENT` only if the catalog already references and verifies that object; otherwise publication resumes registration after re-verification. Conflicting bytes at the same key fail without replacement.

Stages are callable independently for inspection and recovery. A batch command may orchestrate them in order, but persists per-artifact state so an interrupted batch resumes at the last verified stage. A failed later month does not roll back or delete earlier valid immutable partitions.

### Local Freqtrade behavior

The adapter uses native Freqtrade Feather loading where it provides correct format and candle-type mapping. The adapter must not call a cleanup routine that groups duplicate timestamps, forward-fills, or synthesizes candles before quality checks. Any normalization is in-memory and lossless for the supported canonical contract. In particular, native mark-candle `close` maps to canonical `mark_price` without changing source bytes.

Local discovery reports exact paths under explicitly configured source roots. It does not infer exchange or market type from an ambiguous path; the caller supplies that scope. Source path and catalog path ancestors are checked for symlink traversal. Local `read` is read-only.

### Binance Vision behavior

The adapter reuses the existing importer’s fixed hosts, monthly archive naming, checksum sidecar verification, bounds checks, and parser. It uses a separate reviewed crypto-symbol allowlist (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT) and does not expand it from object-store listings. The existing TradFi catalog remains unchanged. `--discover`/plan remains separate from retrieval. An archive being listed or downloaded is not by itself proof of valid data; parsing, time bounds, price checks, and expected cadence still run before publication. The optional 1m experiment is restricted to BTCUSDT and ETHUSDT and requires an explicit opt-in.

The adapter does not widen the manually reviewed instrument catalog based on object-store listings. It does not call Binance FAPI or use API credentials. Vision source coverage is USD-M Futures OHLCV only; Funding and OI remain local-source datasets in this PR.

## Normalized storage and provenance

### Layout

Normalized data is stored under a separate configured data root, for example:

```text
normalized-v1/
  binance-futures-btcusdt-ohlcv-5m/
    year=2025/month=01/part-<content-fingerprint>-<provenance-key>.parquet
```

Each object contains one complete UTC calendar month unless the request explicitly selects a bounded partial interval. OHLCV completeness means exactly one observation per expected interval timestamp across the requested bounds, after applying the source contract's documented monthly boundary convention. Sparse event datasets (Funding/OI) have no inferred cadence: completeness means all source observations in the exact source artifact bounds were preserved and validated; the plan never treats mere event presence as complete normalized coverage. The canonical timestamp is UTC and has a stable nanosecond representation. The canonical columns follow the v3 data-kind contract; source-specific extra columns are retained only when declared by that contract/version. Parquet output is immutable and is never used as a mutable cache file.

For published partitions, catalog v3 records `storage_format="parquet"` and a relative path under the normalized root. Existing Feather partitions omit this additive field and retain the current Feather interpretation. The catalog validator selects its reader from this explicit format field, validates the normalized Parquet byte hash, and recomputes the canonical observation fingerprint. `source_sha256` continues to bind the registered partition file; an additive `provenance.raw_source_sha256` records the original ZIP or Feather bytes. The v3 schema addition is optional so existing manifests remain valid and unchanged.

### Provenance

Each manifest and partition records:

- adapter name and adapter/converter version;
- provider, market type, exchange-native symbol, data kind, and interval label;
- source archive key/URL or local source-root ID and relative path;
- raw archive SHA-256 (for Vision) or original Feather SHA-256 (for local input);
- UTC retrieval time for downloaded inputs;
- normalized Parquet SHA-256, row count, first/last observation, and canonical content fingerprint;
- validation status, issue codes, requested month bounds, and observed bounds.

For normalized Parquet, the partition’s `source_sha256` is the Parquet file hash. Its provenance extension stores the original archive/Feather hash, retrieval time where applicable, converter version, and source locator. This keeps validation unambiguous while preserving raw-input lineage.

Volatile retrieval time is metadata and excluded from the canonical content fingerprint. The content fingerprint hashes canonical schema plus ordered normalized values. A separate provenance key hashes raw source digest, adapter/converter version, schema version, and source locator. Both are included in the immutable object key, so repeat conversion of identical bytes under the same versions and locator is a no-op while equal rows from a different source or converter retain separate lineage.

Catalog v3 remains append-only. Each new manifest revision is a complete snapshot of the dataset's active partition references, carrying forward prior verified non-overlapping partitions and adding the new partition; prior manifests and files remain addressable. Existing raw Feather coverage does not satisfy a normalized-Parquet plan and therefore never suppresses conversion. `PRESENT` means the required interval is covered by verified Parquet partitions (or explicitly accepted partial Parquet segments for a partial request). Overlapping partial-month data cannot silently replace or merge a published partition: it is rejected as a conflict or published under a separately versioned dataset identity. A version change that alters instrument mapping or schema uses a distinct dataset identity/schema contract rather than rewriting prior partitions.

## Incremental planning and status semantics

The plan stage intersects the requested half-open interval with UTC calendar months and compares each required month to the latest valid catalog snapshot. It does not infer availability from filenames alone or treat source Feather as normalized coverage. For OHLCV, a complete partition must satisfy expected cadence with no missing timestamps. For sparse event data, `PRESENT` requires a verified normalized partition representing the full exact local source artifact bounds; no cadence is invented and no absent period is called complete. For each month, return one of:

- `PRESENT`: verified immutable Parquet partition(s) cover the requested bounds under that data-kind completeness rule;
- `MISSING`: source is known or expected but no local partition exists yet;
- `UNAVAILABLE`: the configured source confirms that no archive exists for this month;
- `INVALID`: a local published partition or source observation fails integrity/contract checks;
- `PLANNED`: a source artifact is available and would be downloaded/converted if execution is enabled.

Execution results add `DOWNLOAD_FAILED`, `CHECKSUM_FAILED`, `PARSE_FAILED`, `QUALITY_FAILED`, and `PUBLISH_FAILED` with stable issue codes. `UNAVAILABLE` is not conflated with a failed request. No failure path creates zero rows, marks an absent month `AVAILABLE`, or overwrites a prior partition.

The request interval remains `[start, end)` and must be timezone-aware UTC. Vision monthly archives contain completed UTC months; an incomplete current month is not advertised as a complete archive. Local Feather reads may cover partial months and preserve exact observed bounds.

## CLI and trust boundary

The existing `data-engineering` CLI is the only exposed execution boundary. Add commands with explicit `--adapter`, `--catalog-root`, `--source-root`/`--raw-root`, `--normalized-root`, symbol, kind, timeframe, and interval arguments:

- `sources discover`: metadata-only source availability report;
- `sources plan --dry-run`: gap and expected-work report; bounded metadata discovery is allowed, archive payload downloads and writes are not;
- `sources download`: retrieve eligible missing Vision archives into raw cache;
- `sources validate`: run parsing and quality checks without publication;
- `sources publish`: publish previously validated artifacts;
- `sources sync`: an explicit convenience command that orchestrates the same stages and writes resumable state.

The download/publish commands remain maintainer-operated local CLI actions. PR-03 does not add an MCP tool, Agent tool, web endpoint, worker filesystem capability, or campaign-controlled download permission. Filesystem ownership and access to the host CLI are the trust boundary for this PR; a future authenticated Data Maintainer role is a separate service design. CLI output reports the actor boundary as operator-controlled without claiming platform-enforced identity.

## Safety and failure handling

- Only the bounded public S3 listing endpoint is used for Vision metadata; archive and checksum payloads are fetched from the fixed Vision archive host. Redirect destinations are revalidated against the allowlist, and no arbitrary URL input is accepted.
- Requests use bounded connect/read timeouts, retry only transient responses, cap response and archive sizes, and verify the official checksum before parsing.
- Raw ZIPs are content-addressed and retained for reproducibility; the source Feather file is never modified.
- Source and destination path roots reject symlink ancestors and traversal. Temporary outputs stay inside their configured destination root.
- Publication uses a unique staging file plus atomic no-overwrite creation. A crash leaves either a staging artifact that can be inspected/recovered or a complete immutable final artifact; it cannot expose a partial Parquet file.
- Every batch has a stable request ID and a per-month journal. A validation receipt binds source SHA-256, normalized artifact SHA-256, converter/schema versions, dataset identity, and exact interval. Publication rechecks all bound digests and fields. Journal states are `DISCOVERED`, `DOWNLOADED`, `VALIDATED`, `OBJECT_PUBLISHED`, `CATALOG_REGISTERED`, and terminal failure states; recovery of `OBJECT_PUBLISHED` verifies the object then completes catalog registration, never reporting `PRESENT` before registration.
- Rate, retry, and concurrency limits are conservative and configurable within hard upper bounds. Initial download execution is sequential; concurrency tuning is not a PR-03 requirement.
- Data parsing and validation occur before catalog registration. A validation failure is recorded in the local run report and does not publish a catalog entry that claims valid coverage.

## Compatibility

- Keep ResearchRequest v2, CampaignPolicy v1, legacy catalog v1/v2, and Data Catalog v3 readable without changing their existing contracts.
- Extend v3 catalog registration only through additive optional provenance fields or a versioned extension; do not reinterpret existing v3 Feather fingerprints.
- Keep `scripts/binance_vision_tradfi.py` invocation compatibility. Refactor common parsing/checksum routines behind the adapter only if existing script CLI and tests remain behaviorally compatible.
- Preserve existing CLI commands and package boundaries. The standalone data-engineering wheel declares every runtime format dependency needed by the adapter, including Parquet support.

## Acceptance

1. Adapter contract tests demonstrate that local and Vision adapters expose the same identity/read/validation shape while unsupported capabilities fail explicitly.
2. Offline tests cover discovery, dry-run gap detection, no-op for existing partitions, month-specific download selection, 404 versus transport failure, checksum mismatch, oversized or malformed archives, invalid values, timestamp/coverage gaps, and idempotent resumption.
3. The existing Vision parser/checksum tests continue to pass unchanged or with only necessary adapter-boundary updates.
4. Local Spot/Futures Feather discovery covers OHLCV, Funding, Open Interest, and mark price without modifying source bytes and without cleaning away duplicate/order errors.
5. Valid normalized observations publish monthly Parquet with reproducible content fingerprints and complete provenance; repeated conversion of identical source bytes and converter version is a no-op.
6. Existing normalized partitions are skipped, and only missing eligible months are downloaded. Known missing source months return `UNAVAILABLE`; a simulated network failure returns a distinct failure status.
7. Catalog v3 validates the published Parquet source/partition hashes and coverage; invalid or interrupted publication never reports `AVAILABLE`.
8. A small actual archive acceptance (when available from the configured Vision host) records exact source URL, checksum, converter version, generated fingerprint, and status in an acceptance report. Synthetic and actual evidence remain separately labeled.
9. Existing v1/v2, PR-01, and PR-02 suites plus governance/freeze checks pass.

## Risks and mitigations

- **Vision archive terms or availability change:** use the existing documented public-archive source and preserve its noncommercial/research-use warning; fail closed on missing checksums or changed archive shape.
- **Wrong symbol mapping:** require reviewed exchange-native symbols and explicit canonical identity; never infer TradFi or settlement asset from archive presence.
- **Large conversions:** process one month at a time, use columnar buffers, cap worker memory, and persist a per-month journal.
- **Parquet reproducibility:** pin the converter contract and schema version, exclude volatile metadata from canonical fingerprints, and test repeated conversions under the supported runtime.
- **Duplicate or out-of-order rows:** validate the source sequence before any canonical sort; do not deduplicate or resample to make publication succeed.
- **Unenforced maintainer identity:** keep commands local and absent from MCP/Agent exposure; document that host filesystem and CLI access are the boundary until a separately approved auth service exists.
- **Partial source periods:** retain exact observed bounds and mark incomplete/partial coverage; never interpret an unavailable month as an empty-but-valid Parquet partition.
