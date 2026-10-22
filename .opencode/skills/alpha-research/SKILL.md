---
name: alpha-research
description: Use when proposing, executing, or reviewing a Python factor through the local Alpha Lab MCP service, including multi-symbol/multi-timeframe runs, split-aware Discovery, Validation, Holdout/OS requests, coverage audits, and queued or NOT_EVALUABLE task diagnosis.
compatibility: opencode
metadata:
  opencode/slash: "true"
---

# Alpha Research

Run a bounded, reproducible Python-factor research cycle through the authenticated `alpha-lab` MCP server. The trusted service submits arbitrary factor source to its isolated Docker Worker and records completed results in Evidence Store.

## Required MCP workflow

Use the seven `alpha-lab` tools in this order. A submitted task is not an execution: **`QUEUED` means the Worker has not run yet. Always call `run_factor_v2` with the returned `task_id` before checking for a result.** There is no background queue consumer.

1. `get_history_v2(limit=100)`: inspect recent tasks and Evidence before choosing a hypothesis. Avoid presenting a repeated source or already-tested hypothesis as new.
2. State one falsifiable mechanism, expected sign, factor inputs, prediction horizon, timeframe, date interval, and rejection criterion. Keep the horizon in bars and distinct from the candle timeframe.
3. `get_data_coverage(dataset_key, start, end)`: check the exact Catalog alias and requested UTC interval. Proceed only when it returns `AVAILABLE`. The local data-expansion campaign authorizes BTC/ETH/SOL/BNB/XRP, 5m/15m/1h/4h, spot/perpetual OHLCV, Funding Rate, Open Interest, and Mark Price; actual availability still depends on what is registered in Catalog. Select `split` from the requested dates using the campaign mapping below; policy authorization does not imply local coverage. Use the batch pattern in **Coverage audit and known local inventory** rather than one call per alias.
4. `create_research_request_v2(request={...})`: create the policy-bound request. Use the exact fields below; preserve the authorized `request_id`, timeframe, and interval in later calls.
5. `submit_factor_v2(research_request_id, source, params, execution_mode="stateless")`: send factor source text and bounded parameters. Source must define `compute_factor(history, *, as_of, params)` and return a finite numeric value for each authorized symbol, for example `{"BTCUSDT": value}`. The Worker receives only the declared inputs. Do not access files, network, environment variables, Docker, or host resources from factor code.
6. **Immediately call `run_factor_v2(task_id)` using the task ID returned by submission.** Wait for this MCP call to finish; it performs the bounded Docker execution. Do not stop after `QUEUED`, do not only poll status, and do not leave submitted tasks for a nonexistent background worker.
7. `get_task_status(task_id)`: require `COMPLETED`. If it reports `QUEUED`, invoke `run_factor_v2` with that same task ID; if it reports `RUNNING`, wait briefly and check again. If an execution call timed out or its response was lost, inspect status and resume the same task ID rather than creating a duplicate request. For `FAILED`, `EXECUTION_BLOCKED`, or a tool/transport error, record the returned status and error code, inspect the task report/history, and stop that candidate rather than claiming success or blindly creating duplicates.
8. After `COMPLETED`, call `get_task_report(task_id)` and `get_history_v2(limit=100)`. Require a nonempty `run_id` and `evidence_hash`; match them to the completed task in history. Report metrics and counterevidence from this returned Evidence.

## Multi-symbol and multi-timeframe requests

Verified against the live service: 5 symbols × 15m × 7 days completed with `coverage_rate 1.0`, `3360/3360` valid rows (672 timestamps × 5 symbols), `asset_count {min,median,max} = 5/5/5`.

- **`mode` is chosen by symbol count, not by factor style.** Use `mode: "cross_sectional"` whenever `len(symbols) > 1`. A `time_series` request carries exactly one trusted symbol; submitting a multi-symbol request created as `time_series` returns a bare `{"status":"INVALID_REQUEST"}` with no diagnostic field. If submission fails that way, check mode against symbol count before touching dates or data types.
- **One request has exactly one `timeframe`.** There is no multi-timeframe request. To compare 5m/15m/1h/4h, create one request per timeframe with distinct `request_id`s, run them sequentially, and report them as separate evaluations — never merge them into one continuous report.
- **Keep `data_types` inside local coverage.** A schema binding exists per `(mode, sorted(data_types))`; an unauthorized or unregistered combination fails at submit. Check coverage before adding `open_interest` or non-1h `mark_price` (see inventory below).
- **`history` shape (this is what breaks most first attempts):** one `pandas.DataFrame` with a `(open_time, symbol)` MultiIndex. Each step receives the trailing `lookback_bars` rows *per authorized symbol*, so at most `lookback_bars × len(symbols)` rows, and the frame length equals `lookback_bars` early in the window.
- **Output contract:** return exactly one finite value per authorized symbol, keyed by symbol ID (`{"BTCUSDT": 0.02, "ETHUSDT": -0.01, ...}`). Missing/extra keys, NaN and ±inf are rejected as invalid output.
- **Off-by-one trap:** `if len(frame) <= lookback: return 0.0 for all symbols` fires on every step because the frame is exactly `lookback_bars` long. Guard with `<= lookback + 1` (or compute after `unstack`). This produces a task that *completes* but evaluates as `NOT_EVALUABLE / NO_VALID_CROSS_SECTIONS`, which is easy to misread as missing data.
- Single-symbol `time_series` keeps `history["close"]` as a `Series`; multi-symbol `cross_sectional` needs `history["close"].unstack("symbol")`.

Cross-sectional template (returns values for every authorized symbol and never raises on short history):

```python
def compute_factor(history, *, as_of, params):
    lookback = int(params["lookback"])
    close = history["close"].unstack("symbol")   # index=open_time, columns=symbols
    if len(close) <= lookback + 1:
        return {str(s): 0.0 for s in close.columns}
    ret = close.iloc[-1] / close.iloc[-lookback - 1] - 1.0
    out = {}
    for symbol, value in ret.items():
        v = float(value)
        out[str(symbol)] = v if v == v and abs(v) != float("inf") else 0.0
    return out
```

## Coverage audit and known local inventory

Audit in one batch instead of alias-by-alias: enumerate `{spot,futures} × {BTC,ETH,SOL,BNB,XRP} × {5m,15m,1h,4h} × ohlcv`, plus `funding_rate:event`, `open_interest:event` and `mark_price:{tf}`, issue the `get_data_coverage` calls concurrently, then group results by `status`. Reading the answer:

| `status` | Meaning | Action |
|---|---|---|
| `AVAILABLE`, `missing_interval_count: 0` | complete for the exact window | proceed |
| `AVAILABLE` but `observed_cadence` ≠ requested | alias registered at a different cadence | pick the alias matching the timeframe |
| `PARTIAL` + `coverage_basis: "observed_only"` | sparse event series with no declared calendar | usable only if `missing_interval_count == 0` and count ≈ expected events |
| `MISSING`, `observed_count: 0` | alias registered but empty for that window | treat as no data; do not silently shrink the window |
| `NOT_CONFIGURED` | alias not registered at all | that type/timeframe does not exist locally |
| `OUTSIDE_AUTHORIZED_CAMPAIGN_SPLIT` | dates outside the policy split | fix dates/split, do not rescale silently |

Completeness arithmetic: 5m = 288 bars/day, 15m = 96, 1h = 24, 4h = 6. A full 2024 year is therefore 105,408 / 35,136 / 8,784 / 2,196 rows. An exact count with zero missing intervals is the strongest signal; a shorter count with zero missing intervals means the dataset starts later, not that it is damaged.

Local inventory (probed 2026-10-22 — re-verify with the batch audit before relying on it):

- `futures:ohlcv` and `spot:ohlcv`: all 5 symbols × 5m/15m/1h/4h **available**, 2024 complete to the row.
- `futures:mark_price`: **1h only** (5 symbols, 2024 complete). 5m/15m/4h return `NOT_CONFIGURED`.
- `futures:funding_rate:event`: 5 symbols, 8h cadence (3 events/day), no gaps in probed windows. Status reads `PARTIAL` purely because coverage is observed-only.
- `futures:open_interest`: **no data**. BTC `:event` is registered but returns `MISSING` with 0 rows for 2022–2025 windows; other symbols and frames are `NOT_CONFIGURED`. Do not base a hypothesis on OI until an operator registers OI files.

## Failure triage

| Symptom | Likely cause | Fix |
|---|---|---|
| submit returns bare `INVALID_REQUEST` | multi-symbol request in `time_series`, or an unregistered `(mode, data_types)` binding | switch to `cross_sectional`; trim `data_types` to covered types |
| completed but `NOT_EVALUABLE` / `NO_VALID_CROSS_SECTIONS` with `valid_rows == requested_rows` | constant or degenerate factor values (off-by-one guard, one value broadcast to all symbols) | fix the factor, keep cross-sectional variation; data is fine |
| `asset_count.min == 0` | some timestamps lack a full cross-section | audit per-symbol coverage for the same window |
| `INSUFFICIENT_COVERAGE` | rows genuinely missing | re-audit that symbol × timeframe × window |
| `INSUFFICIENT_VALID_CROSS_SECTIONS` | fewer than 2 usable timestamps | widen the interval, not the symbol set |
| `EXECUTION_BLOCKED` / `ISOLATION_NOT_VERIFIED` | Worker readiness gate rejected | record the code, stop the candidate, do not retry blindly |

## MCP interface reference

Call the bare MCP tool names below. In OpenCode permission rules they appear as `alpha-lab_<tool>`. Pass the arguments by the exact names shown; every response is a JSON object. Keep each returned ID and pass it to the next call.

### 1. `get_history_v2(limit=100)`

Input: `limit` is an integer from 1 to 100; it defaults to 100. Example: `{"limit": 20}`.

Success shape: `{"entries": [...], "source_status": {...}, "source_errors": {...}}`. Each entry can contain `kind`, `identity`, `store_id`, `protocol_version`, and `payload`. A Python Factor v2 task entry has `kind: "TASK"`, `identity: <task_id>`, and a payload with `request_hash` and `status`. Its Evidence entry has `kind: "EVIDENCE"`, `identity: <run_id>`, and a payload with `evidence_hash`, `status`, and `report`. `source_status` reports which history stores were `AVAILABLE`, `NOT_CONFIGURED`, or `UNAVAILABLE`; an empty `source_errors` is normal. This is a bounded newest-first merged history, so older matching records may require a smaller research interval or a targeted report lookup; do not infer missing evidence from absence in a truncated list.

### 2. `get_data_coverage(dataset_key, start, end)`

All three inputs are strings. `dataset_key` is the exact registered alias, shaped as `binance:{spot|futures}:{ASSET}:{ohlcv|funding_rate|open_interest|mark_price}:{TIMEFRAME|event}`. Examples: `binance:futures:ETH:ohlcv:15m`, `binance:spot:ETH:ohlcv:15m`, `binance:futures:ETH:funding_rate:event`, `binance:futures:ETH:mark_price:1h`. Use the exact alias returned by Catalog/coverage; an example is not proof that data exists. `start` and `end` are UTC ISO 8601 timestamps, with `Z` preferred, and describe the half-open interval `[start, end)`.

Success returns coverage metadata such as `status`, `dataset_id`, `requested_interval`, `observed_intervals`, `missing_intervals`, `observed_count`, `partition_versions`, `observed_cadence`, and `coverage_basis`. For candle series continue only for `status: "AVAILABLE"` and a matching requested interval. `PARTIAL` is acceptable only for a sparse event series with `coverage_basis: "observed_only"` and `missing_interval_count: 0` (normal funding-rate shape); every other `PARTIAL`, plus `MISSING`, `OUTSIDE_AUTHORIZED_CAMPAIGN_SPLIT`, `AUTHORIZATION_DENIED`, `INVALID_REQUEST`, or `UNAVAILABLE` means stop and report the returned status; do not adjust the dates without saying so.

### 3. `create_research_request_v2(request)`

Input: one JSON object with exactly the 15 fields below. Generate a unique `request_id` (for example, `btc5m-` plus a UUID); timestamps must be UTC ISO 8601 strings. `label_horizon_bars` and `embargo_bars` are bar counts, not durations.

```json
{
  "protocol_version": "2.0-research-request",
  "request_id": "btc5m-unique-id",
  "campaign_id": "dataset_builder_acceptance",
  "policy_version": "2",
  "campaign_policy_sha256": "e42f7d34d03fe80c03fd09a771b97c90a5479cecb3e87463d5f1c5b6851404f2",
  "start": "2024-01-01T00:00:00Z",
  "end": "2024-01-31T00:00:00Z",
  "symbols": ["ETHUSDT"],
  "timeframe": "15m",
  "mode": "time_series",
  "data_types": ["futures_ohlcv", "spot_ohlcv", "funding_rate", "open_interest", "mark_price"],
  "split": "discovery",
  "lookback_bars": 20,
  "label_horizon_bars": 3,
  "embargo_bars": 1
}
```

Success shape: `{"status":"AUTHORIZED","request_id":"...","research_request_sha256":"...","campaign_id":"...","campaign_policy_sha256":"...","start":"...","end":"...","symbols":[...],"timeframe":"...","mode":"...","data_types":[...],"split":"discovery"}`. Pass this exact `request_id` to `submit_factor_v2`. `INVALID_REQUEST` or `AUTHORIZATION_DENIED` is a failed request; do not submit a factor against it.

#### Choose the split for the requested dates

The trusted campaign uses half-open UTC windows. Set `split` to the window containing the entire requested interval `[start, end)`:

| Requested interval | `split` | Use |
|---|---|---|
| `[2022-01-01, 2025-01-01)` | `discovery` | Reused development data |
| `[2025-01-01, 2026-01-01)` | `validation` | Validation data, only when the request asks for it |
| `[2026-01-01, 2027-01-01)` | `holdout` | Out-of-sample (OS) data; use only for an explicit OS evaluation request |

For example, a February 2025 request must set `start: "2025-02-01T00:00:00Z"`, `end: "2025-03-01T00:00:00Z"`, and `split: "validation"`. A February 2026 OS request uses the same dates in 2026 and `split: "holdout"`. Keep the other request fields from the full schema example. A request crossing a split boundary is denied; split it only when the user approves separate, independently identified research runs.

The campaign policy v2 authorizes five symbols and four candle periods, but Catalog coverage is authoritative for what can actually run. To request Spot/Perp basis, include both `spot_ohlcv` and `futures_ohlcv`; the trusted builder aligns both by PIT availability and exposes `spot_perp_basis` (perp/spot minus 1) and `spot_perp_basis_absolute` to the Worker. Sparse Funding/OI events remain sparse and are as-of joined with bounded age; Mark Price is aligned as a candle series. Never bypass `get_data_coverage` or substitute another source silently.

When an already-downloaded Freqtrade Feather file is not in Catalog, do not download it again or copy/edit the source file. An authorized maintainer can register the existing directory idempotently with `python -m data_engineering --root <catalog-root> catalog-v3 import-local --datadir <freqtrade-datadir> --source-root-id freqtrade-local --existing-source-root alpha-lab-normalized=<normalized-root>` (also pass every other existing Catalog source root); then restart the local service so its read-only source mount and Catalog view are refreshed. Supplying existing roots lets the importer validate and skip verified datasets instead of attempting to re-register them. This operation reads and registers local files only; a `SKIPPED_PRESENT` result means the verified dataset was already present. Never register or edit data from an Agent research run.

### 4. `submit_factor_v2(research_request_id, source, params, execution_mode)`

Inputs: `research_request_id` is the authorized ID from step 3; `source` is nonempty Python source text defining `compute_factor(history, *, as_of, params)`; `params` is a JSON object with at most 64 named scalar values (string, integer, finite float, or boolean; no arrays, objects, or null); `execution_mode` is `"stateless"` or `"stateful"`. Use `stateless` for ordinary one-window factors. Every returned symbol value must be finite numeric. Example params: `{"lookback":12}`.

```python
def compute_factor(history, *, as_of, params):
    lookback = int(params["lookback"])
    close = history["close"]
    if len(close) <= lookback:
        return {"BTCUSDT": 0.0}
    return {"BTCUSDT": float(close.iloc[-1] / close.iloc[-lookback - 1] - 1.0)}
```

Success shape: `{"task_id":"...","request_hash":"<sha256>","status":"QUEUED"}`. Save `task_id`; `QUEUED` means accepted for execution, not completed. `INVALID_REQUEST`, `AUTHORIZATION_DENIED`, or `UNAVAILABLE` is not a successful submission.

Submission is idempotent per `(request, source, params)`: the `task_id` is the truncated identity hash, so resubmitting identical text returns the same ID (resume it, do not duplicate). Reusing one authorized `request_id` with different source or params is supported and yields a new task; never reuse a `request_id` for a different interval, `timeframe`, symbol set, or `mode`. Multi-symbol (`cross_sectional`) factors must return a mapping for every authorized symbol — use the template in **Multi-symbol and multi-timeframe requests**.

### 5. `run_factor_v2(task_id)`

Input: the `task_id` from step 4, as a string. This is an awaited execution call. Do not start another candidate before it returns.

On success the result includes `task_id`, `run_id`, `status` (normally `EXPLORATORY`), `evidence_hash`, and `report`. A readiness rejection returns `{"task_id":"...","status":"EXECUTION_BLOCKED","error_code":"ISOLATION_NOT_VERIFIED"}`. Treat any blocked, failed, or unavailable response as a failed run, preserve its `error_code`, and do not claim Evidence was produced. After success, still verify with steps 6–8.

### 6. `get_task_status(task_id)`

Input: the same task ID string. Existing task responses contain `task_id`, `status`, `request_hash`, `plan_hash`, `chunk_count`, and `created_at` (fields may be omitted if unavailable). Terminal success is `status: "COMPLETED"`; `QUEUED` and `RUNNING` are not completion. Unknown IDs return `{"status":"NOT_FOUND","error_code":"TASK_NOT_FOUND"}`. If status is still queued, call `run_factor_v2` for that same ID; do not resubmit.

### 7. `get_task_report(task_id)`

Input: the same task ID string. Before completion, this returns only `{"task_id":"...","status":"QUEUED|RUNNING|..."}`. After completion, success shape is `{"task_id":"...","run_id":"...","status":"EXPLORATORY","evidence_hash":"<sha256>","report":{...},"formal_inference_status":"BLOCKED"}`. The `report` object contains the descriptive metrics produced by the evaluator; read and quote the returned fields rather than inventing defaults. A missing or invalid persisted Evidence returns `status: "UNAVAILABLE"` with an `error_code`, and does not count as completion.

Reports can be large: `report.metrics.per_time` holds one entry per evaluated timestamp (hundreds to thousands of rows). Extract only `coverage`, `asset_count`, `effective_cross_sections`, `mean_cs_ic`, `mean_cs_rank_ic`, `monthly`, `turnover` and `quantile_returns`; never paste `per_time` into a summary. A non-`EXPLORATORY` `status` is a diagnosis, not a failure of the data — read `report.reason` first.

Then call `get_history_v2` again. Match the `TASK` entry whose `identity` equals `task_id` and whose payload status is `COMPLETED`; match the `EVIDENCE` entry whose `identity` equals `run_id` and whose `payload.evidence_hash` equals the report hash. If the requested limit omitted those entries, increase the limit up to 100 and retry the history read.

Create requests with exactly these fields:

```json
{
  "protocol_version": "2.0-research-request",
  "request_id": "unique-id",
  "campaign_id": "dataset_builder_acceptance",
  "policy_version": "2",
  "campaign_policy_sha256": "e42f7d34d03fe80c03fd09a771b97c90a5479cecb3e87463d5f1c5b6851404f2",
  "start": "UTC timestamp",
  "end": "UTC timestamp",
  "symbols": ["ETHUSDT"],
  "timeframe": "15m",
  "mode": "time_series",
  "data_types": ["futures_ohlcv", "spot_ohlcv", "funding_rate", "open_interest", "mark_price"],
  "split": "discovery",
  "lookback_bars": 20,
  "label_horizon_bars": 3,
  "embargo_bars": 1
}
```

The Discovery policy covers `[2022-01-01, 2025-01-01)`; registered coverage differs by symbol, timeframe, and data type. Use the interval requested by the user. If none is specified, review history, choose and disclose a 30-day Discovery pilot that fits the hypothesis, and label it as a pilot; do not silently shorten a requested interval or claim a pilot represents the full period. Large requests can exceed the bounded Worker budget. If needed, explain the limit and get approval before splitting into separately identified requests; do not merge separate reports as if the service evaluated one continuous run.

## Interpretation and boundaries

- Treat 2022–2024 Discovery as reused development data, never as fresh out-of-sample evidence. The service returns descriptive `EXPLORATORY` reports; `formal_inference_status: BLOCKED` is expected and must remain visible.
- Report IC, Rank IC, coverage, turnover, grouped forward returns, sample interval, timeframe, horizon, source hash, dataset identity, worker image digest, run ID, and Evidence hash when present. State supporting and opposing results. Do not call descriptive statistics executable PnL or a profitable factor.
- Use one candidate at a time. `run_factor_v2` executions are serialized by the service. For an explicitly requested finite batch, run candidates sequentially and record each request and Evidence independently; do not launch a queue and assume parallel execution.
- Choose `discovery`, `validation`, or `holdout` according to the requested date window. Treat 2025 validation as validation evidence, not Discovery evidence. The 2026 holdout is available only for an explicit OS evaluation request and must not be used for factor selection or tuning. Do not bypass MCP with direct database edits, local data reads, shell execution of candidate Python, or alternate evaluators.
- Do not edit Alpha Lab, Freqtrade, tests, labels, thresholds, campaign policy, or Evidence Store. This skill grants access only to the authenticated Agent MCP tools. It does not grant access to Broker credentials, raw data paths, Factory configuration, Docker, or the Docker socket.
- A task is complete only after `run_factor_v2` has returned, status is `COMPLETED`, and the report's `run_id` and `evidence_hash` match persisted history. Submission, `QUEUED`, Worker output alone, or mock data is not completion.

## Handoff

Return the hypothesis and rejection criterion; timeframe, horizon, interval, and dataset alias; request ID and task ID; execution and terminal statuses; run ID and Evidence hash; source hash; coverage; key metrics and counterevidence; reused-data and inference limitations; and any blocker. If the task is still `QUEUED`, call `run_factor_v2` before handing off.
