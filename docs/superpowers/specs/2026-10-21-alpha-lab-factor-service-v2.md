# Alpha Lab Python Factor Service v2 — Design Specification

**Status:** Draft for user review
**Scope:** PR-05 in `pasted-text-1.txt` (Python Factor Service v2)
**Architecture approval:** Recommended design approved by user on 2026-10-21
**Depends on:** ResearchRequest v2 / CampaignPolicy v1, Data Catalog v3, source adapters, DatasetBuilder v2 (PR-01–04)

## 1. Goal

Run trusted, authorized factor research over windows longer than the current 512 as-of limit while keeping each untrusted Worker invocation bounded and isolated. The service must support typed point-in-time inputs, deterministic chunking, explicit state transfer for stateful factors, recoverable retries, and complete-only Evidence publication. The existing v1 route, request schema, Worker contract, and Store remain compatible.

This delivers a computation protocol only. It does not open formal statistical inference, live trading, data download, or broader Agent permissions.

## 2. Existing contracts to reuse

- `ResearchRequestV2` and the trusted `CampaignPolicyRegistry` authorize campaign, split, date window, symbols, timeframe, mode, data types, lookback, label horizon, and embargo. v2 must accept or resolve this trusted request identity; Worker-supplied authority is never trusted.
- `DatasetBuilderV2` produces `WorkerInputV2`, evaluator-only labels, pinned manifests, and fingerprints. Factor Service v2 consumes the worker-safe product and never reconstructs data from caller-provided paths.
- `WorkerInputV2.asof(decision_time)` is the source of each causal input frame. Evaluator labels remain outside the Worker process and its input stream.
- Protocol v1 and the current 512-step resource ceiling remain unchanged. v2 is explicitly versioned and additive.

## 3. API and identity

Introduce an explicit `FactorRequestV2` (and v2 run endpoint/tool) containing:

- a trusted `research_request_id` resolved through CampaignPolicyRegistry; the caller cannot submit an alternate authority payload;
- factor source bytes and bounded scalar parameters;
- a registered worker-input schema identifier/version;
- requested execution mode (`stateless` or `stateful`) and, for stateful factors, a bounded state schema/version;
- no resource-profile field; v2 always uses the single server-configured immutable Worker profile, never a client image, command, mount, path, or resource limit.

No caller-supplied symbol, time, split, source adapter, data path, label, or authorization field may override the resolved ResearchRequest/Campaign policy. Unknown fields fail closed. The request identity hashes canonical source, parameters, trusted ResearchRequest and policy identity, DatasetBuilder manifest/fingerprint, typed input schema, factor/state contract versions, immutable Worker image digest, evaluator version, and service protocol version. A repeat submission with the same identity returns the same task; a changed input produces a different identity.

The v1 `/v1/run`, `FactorRequest`, v1 Worker framing, existing Store, and their validators are not silently widened. v2 gets separate models/routes and additive versioned persistence.

## 4. Typed point-in-time Worker inputs

The service resolves a versioned schema from a trusted registry. A schema declares field name, logical type, units/meaning, source data type, availability rule, null policy, and allowed shape. Initial registration covers the PR-04 worker-safe OHLCV, Spot/Futures, Funding, and Open Interest features that are present and authorized in `WorkerInputV2`. The service rejects unknown fields, schema mismatches, unavailable fields, and unregistered source/type combinations.

Each serialized Worker input is produced by the trusted DatasetBuilder for an as-of timestamp. It contains no evaluator labels, future rows, protected Store contents, credentials, or host paths. Missing values retain the registered null semantics; absent data is never silently zero-filled. Input serialization is size bounded and canonical.

## 5. Chunking and deterministic execution

- A container invocation may process at most **512 evaluation as-of steps**, preserving the existing per-task Worker safety ceiling.
- The scheduler divides the authorized ordered evaluation timeline into deterministic, non-overlapping chunks. Chunk boundaries and plan hash are derived from the request identity and server-owned chunk policy.
- A separate server-owned total-window limit bounds number of as-of steps, serialized input bytes, state bytes, output bytes, wall time, and cumulative resource use. Initial defaults are implementation-plan decisions and must be named/versioned configuration, not caller-controlled values.
- Each chunk receives only its required point-in-time warmup history plus its evaluation as-of steps. Warmup rows do not emit evaluation outputs and are not counted twice in merged results.
- Output merge validates exact expected timestamps, uniqueness, declared symbols, finite values, and the full plan hash. Missing, extra, duplicate, reordered, or malformed results fail the task.
- A v2 stateless factor is required to produce the same canonical per-as-of outputs whether evaluated as one logical window or through any permitted chunk partition. Tests compare chunked output to a single-process reference; they do not imply executing a >512 step unbounded Worker.

## 6. Stateful factor contract

Stateful code uses a separately versioned Worker API with explicit bounded JSON state input and output. Chunk zero initializes state once from the registered initial state and its causal warmup rows. For each later chunk, the trusted scheduler supplies the checkpointed state from the preceding committed chunk and only advances it over newly assigned evaluation steps. Any additional warmup rows are marked read-only context and cannot be replayed through the state transition. The Worker returns the next state alongside outputs. State is schema-validated, canonicalized, size-limited, and included in chunk hashes.

Warmup phase is explicit: the factor receives causal warmup history but emits no scored outputs. State is initialized from warmup once, before the first evaluation step; later context-only warmup rows do not mutate carried state. State transition semantics must be deterministic for a fixed input, initial state, runtime image, and source. Stateful chunk output must match a sequential reference run over the same input. A retry starts from the last committed checkpoint and cannot apply the same state transition twice.

No implicit in-memory state is preserved between containers. A factor that cannot serialize its complete bounded state is not eligible for stateful mode.

## 7. Task lifecycle, retry, and checkpoints

Add a new store at `user_data/research/alpha_lab/stores/python-factor-v2/`, leaving v1 tables and paths intact. Enforce the existing private-store filesystem rules (0700 directory, 0600 database, no symlink components). Persist immutable task identity and an append-only event history. Persist per-chunk plan identity, attempt number, status, input hash, output hash, state-in/state-out hashes, and bounded diagnostic metadata. Checkpoint payloads are canonical JSON in the private v2 Store; Worker code cannot access the Store directly.

Task and chunk transitions are explicit and monotonic (for example `QUEUED → RUNNING → RETRYABLE_FAILURE → RUNNING → COMPLETE`, or terminal `FAILED` / `EXECUTION_BLOCKED`). Only a failed/incomplete chunk may be retried, and only under the same full task identity and chunk-plan hash. A committed successful chunk is immutable and is never rerun as a state transition. Duplicate submit/run calls are idempotent. Runtime/profile violations fail closed as `EXECUTION_BLOCKED`.

Evidence is created only after every planned chunk is committed, the merged output passes completeness validation, and the trusted evaluator succeeds. An incomplete, failed, canceled, or blocked task cannot append complete Evidence. Evidence insertion and transition to task completion occur in one Store transaction, so a crash cannot leave contradictory completion state. No partial output is exposed as a completed report.

## 8. Worker and Broker security boundary

The v2 Worker remains untrusted and runs in the existing fixed, immutable image/profile with network disabled, no Docker socket, no host mounts, read-only root filesystem, bounded CPU/memory/PIDs/tmp/timeout, and no credentials. Source and serialized Worker inputs are passed only through bounded framed streams or an equivalently isolated channel. The MCP/control-plane process does not gain runtime socket access.

The trusted Broker alone launches the fixed Worker profile. It accepts task/chunk IDs and server-owned input references, not arbitrary paths, mounts, commands, image names, or resource limits. If the required immutable image or isolation runtime is unavailable, v2 execution stays blocked. Real-container negative tests remain a release requirement; protocol unit tests are not security acceptance.

## 9. Compatibility and migration

- All v1 public request schemas, routes, Worker protocol, manifest identity, tests, and data remain byte/behavior compatible.
- v2 tasks use distinct task IDs/schema versions and additive Store tables or a new versioned Store path; no migration rewrites v1 records.
- v1 tasks are not automatically replayed as v2 because their typed-input, source snapshot, and chunk identities are not equivalent.
- Existing v1 evaluator behavior is unchanged. v2 may call the trusted evaluator adapter only after complete output merge; evaluation-mode unification remains PR-06 scope.
- Governance uses a new versioned manifest and preserves the frozen v1.1 manifest bytes.

## 10. Acceptance criteria

1. A one-month BTC 5m ResearchRequest can complete through deterministic chunks within explicit total-window and per-container limits.
2. Chunked stateless output matches its sequential reference; stateful output and final checkpoint match a sequential reference across multiple chunk layouts and a forced restart.
3. Same source/config/Campaign/Dataset/runtime identity is idempotent and reproducible; any material identity change creates a distinct task.
4. Failure injection at every chunk/publication boundary resumes from committed checkpoints without duplicate state transitions or incomplete Evidence.
5. Unknown/unregistered typed fields, unauthorized data, labels, future rows, client paths, image overrides, and oversized state/input/output are rejected.
6. Actual-container tests prove network, socket, protected paths/data, and resource boundaries; unavailable isolation produces `EXECUTION_BLOCKED`.
7. v1 compatibility and v1.1 frozen manifest byte identity are verified.
8. Formal inference remains `BLOCKED`, and no incomplete task can be represented as complete Evidence.

## 11. Out of scope

- PR-06's unified TS/CS report contract and Evidence model, except for the minimal complete-output handoff required by this service.
- PR-07's general Agent authorization and history-retrieval workflow.
- Data acquisition or expansion of the Data Maintainer boundary.
- Formal p-value/confidence-interval qualification, strategy PnL, live trading, or holdout promotion.

## 12. Decisions required before implementation planning

Please review the spec for correctness and scope. In particular, confirm whether:

1. v2 should bind directly to the trusted ResearchRequest/Campaign registry and consume PR-04 `WorkerInputV2` (recommended),
2. the 512-step limit should remain per Worker invocation with a separately configured total-window limit,
3. stateful factors should be supported only through bounded JSON state transfer and checkpointed chunk commits, and
4. v1 compatibility should be preserved through a separate v2 route and additive persistence.

After spec approval, the implementation plan will resolve concrete total-window defaults and task-store schema, then be submitted for approval before code changes.
