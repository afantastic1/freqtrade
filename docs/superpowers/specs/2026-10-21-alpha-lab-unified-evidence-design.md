# Alpha Lab Unified Descriptive Evaluation and Evidence

## Goal and constraints

PR-06 adds one mode-aware descriptive evaluation contract for authorized Python Factor Service v2 runs. It must preserve existing TS/CS evaluators and Stores, keep Evidence and Decision append-only, retain reproducible identity, and leave formal statistical inference `BLOCKED`. It does not qualify a strategy for deployment or release formal inference.

## Architecture

The trusted service resolves the `ResearchRequestV2` and `DatasetBuilderV2` run before evaluation. The request's authorized `mode` selects a dedicated descriptive evaluator:

- `time_series`: a single-symbol factor/label stream with descriptive Pearson IC and RankIC, grouped forward-return summaries, and monthly/yearly stability.
- `cross_sectional`: per-timestamp IC and RankIC, factor-quantile forward returns, effective asset counts, and monthly/yearly summaries.

Both evaluators share a projection contract for valid/missing row counts, coverage, missingness, turnover, and explicit cost-assumption diagnostics. Cost-adjusted returns are diagnostics and must be labeled as assumptions; they are not executable strategy PnL. Unsupported metrics remain absent or null rather than being inferred from a different mode.

Evaluation returns `NOT_EVALUABLE` with a stable reason when labels do not overlap, the minimum observations/coverage are not met, or a mode-specific requirement fails. `NOT_EVALUABLE` is a valid terminal descriptive result and can be stored as Evidence. It must never produce a favorable fallback score. Neither evaluator exposes p-values, confidence intervals, or calibrated inferential claims.

## Evidence identity and persistence

Evidence identity is derived from canonical authorized inputs and evaluator identity, and binds:

- full Campaign identity and policy digest;
- ResearchRequest identity and canonical request digest;
- Dataset manifest digest and dataset fingerprint;
- mode and data/source provenance already pinned by the Dataset manifest;
- source code SHA-256, canonical parameters, and execution protocol;
- worker image digest and evaluator name/version;
- complete ordered output hashes and descriptive report.

The canonical Evidence payload is immutable and append-only. Repeating the same identity and payload returns the existing hash; conflicting content under an existing identity is rejected. Decision persistence remains append-only and references the exact Evidence hash. Historical tables, raw data, and existing v1.1 records are not migrated or rewritten.

## Unified read-only history

Add a read-only facade that queries the legacy research Store and Python Factor Service v1/v2 Stores through small adapters. It returns normalized history summaries with store/protocol provenance and stable pagination/limit bounds. The facade performs no writes, migrations, or inference and must tolerate a configured Store being unavailable only by returning an explicit partial-source status; it must not silently omit a source. Existing Store APIs remain supported.

## Service flow

1. Resolve and revalidate the trusted Campaign, ResearchRequest, pinned Dataset, worker image, and source request.
2. Execute all bounded chunks and verify the complete ordered output schedule.
3. Route outputs to the evaluator selected by authorized mode.
4. Build the canonical provenance-bound Evidence record and atomically publish it only after all chunks are committed.
5. Return the descriptive report, Evidence identity/hash, and `formal_inference_status: BLOCKED`.
6. Expose read-only cross-store history without changing stores.

Incomplete tasks remain ineligible to publish Evidence. Evaluation or persistence failures do not create partial Evidence.

## Compatibility and security

The v1 service, legacy TS/CS evaluators, schemas, and Store layouts remain unchanged. The new contract is additive and versioned. Only trusted service dependencies provide the evaluators and Store adapters. Agent callers cannot select evaluator implementations, alter provenance, read host paths, mutate Catalog/data, or unlock inference.

## Validation

Focused tests cover TS and CS metric routing, stable `NOT_EVALUABLE` reasons, coverage/missingness/turnover, cost assumptions, exact evidence identity/provenance, idempotent replay and conflicts, no Evidence for incomplete tasks, append-only behavior, and unified read-only history across Stores. Integration acceptance uses separate synthetic and real data records; the existing Binance BTCUSDT 5m May 2025 fixture can verify the TS route, while CS uses a trusted multi-symbol dataset. Security checks verify that history is read-only and no formal inference fields are emitted. The v1.1 freeze manifest bytes and historical database hashes are captured and compared before and after acceptance.

