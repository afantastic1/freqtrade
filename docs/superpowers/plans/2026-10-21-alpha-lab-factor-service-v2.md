# Alpha Lab Python Factor Service v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recoverable, typed-input Python factor execution service for authorized ResearchRequest v2 windows while preserving v1 behavior.

**Architecture:** Keep the v2 API, identity, chunk scheduler, state checkpoints, and Evidence lifecycle additive to the existing v1 service. Resolve all authority and input schemas on the trusted control plane, send only bounded canonical frames to the fixed Broker profile, and publish Evidence atomically after all chunks and evaluation succeed.

**Tech Stack:** Python 3, Pydantic, pandas, SQLite, MCP FastMCP, Docker Broker, pytest, existing Alpha Lab contract and DatasetBuilder APIs.

**Spec:** `docs/superpowers/specs/2026-10-21-alpha-lab-factor-service-v2.md`

## Global Constraints

- Preserve `/v1/run`, `FactorRequest`, v1 Worker framing, v1 Store path/schema, v1 manifests, and v1 tests without behavior changes.
- Keep each Worker invocation at or below 512 evaluation as-of steps.
- Resolve `research_request_id` only through trusted `CampaignPolicyRegistry`; caller payload cannot replace Campaign authority.
- Use `DatasetBuilderV2` and `WorkerInputV2.asof()`; evaluator labels and host paths never enter Worker frames.
- Use only the server-configured immutable Worker image/profile; network is `none`, no host mounts or credentials, read-only root, UID `65532:65532`, memory `512m`, tmpfs `/tmp` `16m`, and at most 64 PIDs.
- Formal inference stays `BLOCKED`; v2 Evidence remains `EXPLORATORY` or `DIAGNOSTIC`.
- Add v2 artifacts to versioned governance manifests and preserve frozen v1.1 manifest bytes.

## Review Focus

- Caller attempts to override Campaign symbols, dates, split, source, data types, or labels: reject before task creation; pin in `test_v2_authorization.py`.
- Schema-valid but unavailable, null, or future feature values: preserve registered null semantics and reject unavailable/future rows; pin in `test_v2_inputs.py`.
- Chunk boundary, duplicate, missing, reordered, or extra output: fail closed without complete Evidence; pin in `test_v2_chunks.py`.
- Retry after a crash around state commit: resume only an uncommitted chunk from the prior committed checkpoint; pin in `test_v2_recovery.py`.
- Worker image/runtime unavailable or attempted network/socket/protected-data access: mark `EXECUTION_BLOCKED`; prove against actual container in `test_v2_container.py` and release acceptance.

---

### Task 1: Versioned v2 request, limits, and typed input contracts

**Files:**
- Create: `alpha_lab_service/protocol_v2.py`
- Create: `alpha_lab_service/input_schemas_v2.py`
- Create: `alpha_lab_service/tests/test_protocol_v2.py`
- Create: `alpha_lab_service/tests/test_v2_inputs.py`
- Modify: `alpha_lab_service/__init__.py`

**Interfaces:**
- Produces `FactorRequestV2`, `WorkerInputSchemaV1`, `V2_LIMITS`, `canonical_request_hash_v2()`.
- `V2_LIMITS` is immutable server configuration `factor-service-v2-limits-v1`: 512 evaluation steps/chunk, 100,000 total evaluation steps, 16 MiB serialized input/chunk, 512 MiB cumulative serialized input, 4 MiB output/chunk, 256 MiB cumulative output, 1 MiB canonical state, 1,800 cumulative Worker wall seconds, and 3 attempts/chunk. No limit is request-controlled.
- Schema registry initially exposes only the PR-04 worker-safe OHLCV, Spot/Futures, Funding, and Open Interest columns that the trusted DatasetBuilder actually provides; each entry records logical type, units, source data type, availability rule, null policy, and shape.

- [ ] **Step 1: Write failing model and registry tests** for strict extra-field rejection, UTF-8 source byte bound, scalar finite parameters, valid mode/state schema pairing, limit immutability, unknown fields, and canonical nullable numeric serialization.
- [ ] **Step 2: Run** `pytest alpha_lab_service/tests/test_protocol_v2.py alpha_lab_service/tests/test_v2_inputs.py -q`; confirm the new v2 symbols are absent.
- [ ] **Step 3: Implement** the frozen Pydantic request and registered typed schema models in the two new modules. Use a 256 KiB source cap to match v1 and canonical sorted compact JSON with `allow_nan=False`.
- [ ] **Step 4: Add tests** proving v1 `FactorRequest` validation and v1 canonical hash remain unchanged; run the new tests plus `alpha_lab_service/tests/test_protocol.py`.
- [ ] **Step 5: Commit** as `feat(alpha-lab): define factor service v2 contracts`.

### Task 2: Trusted ResearchRequest resolution and complete v2 identity

**Files:**
- Create: `alpha_lab_service/authorization_v2.py`
- Create: `alpha_lab_service/tests/test_v2_authorization.py`
- Modify: `alpha_lab_service/protocol_v2.py`

**Interfaces:**
- Consumes `FactorRequestV2`, a trusted request-ID registry, existing `ResearchRequestV2`, `CampaignPolicyRegistry`, `DatasetBuilderV2`, server-owned `DatasetBuildOptionsV1`, and Task 1 schema registry.
- Produces `AuthorizedFactorRunV2` and `resolve_authorized_run(request, research_request_registry, policy_registry, dataset_builder, dataset_options, worker_identity, evaluator_identity) -> AuthorizedFactorRunV2`.
- `AuthorizedFactorRunV2` carries the trusted ResearchRequest, Campaign/policy identity, `DatasetBuildResultV2`, resolved schema, ordered evaluation timestamps derived from worker-safe `decision_time`, identity hash, and canonical request record. It is control-plane-only and never serialized wholesale to Worker.

- [ ] **Step 1: Write failing tests** for valid registry resolution and rejection of caller-supplied symbols/time/split/source-adapter/data types/labels/paths/resource profile/authority fields; assert the builder receives only the resolved ResearchRequest.
- [ ] **Step 2: Run** `pytest alpha_lab_service/tests/test_v2_authorization.py -q`; confirm authorization resolver is missing.
- [ ] **Step 3: Implement** exact allowlisted `FactorRequestV2` parsing and trusted resolution. Hash canonical source, scalar params, ResearchRequest and Campaign policy identity, DatasetBuilder manifest/fingerprint, schema ID/version, execution/state contracts, immutable image digest, evaluator identity, limits version, and protocol version.
- [ ] **Step 4: Add identity tests** showing identical inputs return identical identity and changing each material identity component changes it; run the test module and existing ResearchRequest/Campaign policy tests.
- [ ] **Step 5: Commit** as `feat(alpha-lab): bind factor v2 to trusted campaigns`.

### Task 3: Bounded chunk plan and point-in-time frame construction

**Files:**
- Create: `alpha_lab_service/chunking_v2.py`
- Create: `alpha_lab_service/tests/test_v2_chunks.py`
- Modify: `alpha_lab_service/data_feed.py`

**Interfaces:**
- Consumes `AuthorizedFactorRunV2`, `WorkerInputSchemaV1`, and `V2_LIMITS`.
- Produces immutable `ChunkPlanV2` / `ChunkV2` records and `plan_chunks(run) -> ChunkPlanV2`, `build_chunk_frames(run, chunk) -> list[dict]`, and `validate_chunk_output(run, chunk, frames, output) -> list[dict]`.
- Each chunk has at most 512 evaluation timestamps. Boundaries, warmup requirements, plan hash, chunk IDs, and expected timestamps are deterministic from the full run identity and server limit version.
- Every as-of frame comes only from `WorkerInputV2.asof(timestamp)`, uses the registered schema, and is canonical/size-checked. Warmup is marked separately from evaluation and cannot emit scored output.

- [ ] **Step 1: Write failing tests** for an 8,928-step month producing deterministic non-overlapping chunks, a 512/513 boundary, causal warmup, null preservation, input byte caps, labels/future-row exclusion, and malformed/missing/duplicate/reordered/extra output.
- [ ] **Step 2: Run** `pytest alpha_lab_service/tests/test_v2_chunks.py -q`; confirm planner and frame builder are absent.
- [ ] **Step 3: Implement** deterministic partitioning, typed schema serialization, per-as-of `WorkerInputV2.asof()` materialization, and exact output validation. Enforce 16 MiB per chunk and 512 MiB cumulative input before any Worker launch.
- [ ] **Step 4: Add reference parity tests** proving stateless output merge for several permitted partition sizes equals a sequential reference; run chunk and existing data-feed/causality tests.
- [ ] **Step 5: Commit** as `feat(alpha-lab): plan bounded factor v2 chunks`.

### Task 4: Stateful Worker v2 contract and fixed Broker endpoint

**Files:**
- Create: `alpha_lab_service/worker_protocol_v2.py`
- Create: `alpha_lab_service/tests/test_worker_v2_contract.py`
- Create: `alpha_lab_service/broker_client_v2.py`
- Modify: `alpha_lab_service/broker_server.py`
- Modify: `containers/alpha-lab-factor-worker/worker.py`
- Modify: `containers/alpha-lab-factor-worker/Dockerfile`
- Modify: `containers/alpha-lab-factor-worker/profile.json`

**Interfaces:**
- Produces strict v2 `init`, `warmup`, `step`, `checkpoint`, and `end` frame models with protocol version `2.0`; state is canonical JSON up to 1 MiB.
- Produces a separate fixed Broker `/v2/run-chunk` operation accepting only validated task ID, chunk ID, and bounded frames; no paths, commands, image, mounts, or resource values are accepted.
- Worker state transition is `transition(state, evaluation_frame) -> (output, next_state)`; warmup initializes state once for chunk zero, and later warmup frames are read-only context.

- [ ] **Step 1: Write failing protocol tests** for v1/v2 framing separation, invalid state schema/oversized state, warmup immutability on continuation, no duplicate transition, exact output count/timestamp, and rejected Broker override fields.
- [ ] **Step 2: Run** `pytest alpha_lab_service/tests/test_worker_v2_contract.py alpha_lab_service/tests/test_broker.py -q`; confirm only v1 behavior exists.
- [ ] **Step 3: Implement** v2 Worker framing and Broker dispatch while leaving `/v1/run` validation and invocation unchanged. Keep the fixed Docker profile; bump worker image/protocol identity instead of widening v1.
- [ ] **Step 4: Add unit tests** for size and frame caps, plus worker stateless and stateful sequential parity; run targeted protocol, broker, worker contract, and worker profile tests.
- [ ] **Step 5: Commit** as `feat(alpha-lab): add isolated factor worker v2 protocol`.

### Task 5: Separate v2 Store, chunk checkpoints, and atomic publication

**Files:**
- Create: `alpha_lab_service/store_v2.py`
- Create: `alpha_lab_service/tests/test_store_v2.py`

**Interfaces:**
- Produces `ResearchStoreV2` at `user_data/research/alpha_lab/stores/python-factor-v2/`, with metadata, immutable task identity, append-only task events, chunk plan/status/attempt/hash records, bounded canonical checkpoint payloads, and Evidence.
- Produces transaction methods `create_or_get_task()`, `commit_chunk()`, `record_chunk_failure()`, `get_resume_state()`, and `complete_with_evidence()`.
- `complete_with_evidence()` validates all planned chunks are committed and inserts Evidence plus terminal `COMPLETED` event in one SQLite transaction. Failed, canceled, or blocked tasks cannot publish complete Evidence.

- [ ] **Step 1: Write failing Store tests** for 0700 directory/0600 database, symlink rejection, same-identity idempotence, immutable committed chunks, monotonic event transitions, checkpoint hashes, crash-safe retry state, and rollback when Evidence insert fails.
- [ ] **Step 2: Run** `pytest alpha_lab_service/tests/test_store_v2.py -q`; confirm v2 Store is absent.
- [ ] **Step 3: Implement** the distinct v2 schema and atomic transaction operations. Reuse the established private Store path checks, but do not modify v1 tables or default path.
- [ ] **Step 4: Test** injected failures before and after chunk commit and at Evidence publication; run Store v2 and v1 Store tests.
- [ ] **Step 5: Commit** as `feat(alpha-lab): persist factor v2 checkpoints safely`.

### Task 6: Recoverable v2 scheduler and evaluator handoff

**Files:**
- Create: `alpha_lab_service/jobs_v2.py`
- Create: `alpha_lab_service/tests/test_v2_recovery.py`
- Modify: `alpha_lab_service/evaluator.py`

**Interfaces:**
- Produces `ResearchServiceV2.submit_factor_v2(payload)`, `run_factor_v2(task_id)`, and `get_task_v2(task_id)` backed by Tasks 1–5.
- Scheduler executes chunks in order, retries only failed/incomplete chunks up to 3 attempts, resumes state from the last committed checkpoint, and never reruns committed chunks.
- Evaluator adapter accepts only a complete, validated merged factor frame plus the trusted `AuthorizedFactorRunV2`; it returns descriptive report data and cannot receive worker state or partial output.

- [x] **Step 1: Write failing orchestration tests** for 3 attempts maximum, restart at each chunk boundary, state transition applied once, total wall/output limits, duplicate submissions, unavailable image/runtime -> `EXECUTION_BLOCKED`, and no Evidence on any partial failure.
- [x] **Step 2: Run** `pytest alpha_lab_service/tests/test_v2_recovery.py -q`; confirmed orchestration class was absent before implementation.
- [x] **Step 3: Implement** the scheduler with persisted transitions around every Broker call and chunk commit. Enforce cumulative wall time (1,800 seconds) and output bytes (256 MiB); evaluate only after the full merge.
- [x] **Step 4: Add failure injection** around Worker return, checkpoint write, evaluator return, and Evidence transaction; run recovery and existing evaluator tests.
- [x] **Step 5: Commit** as `feat(alpha-lab): resume factor v2 tasks by committed chunk`.

### Task 7: Additive MCP route, trusted factory wiring, and compatibility proof

**Files:**
- Modify: `alpha_lab_service/server.py`
- Modify: `alpha_lab_service/runtime.py`
- Create: `alpha_lab_service/tests/test_mcp_v2_tools.py`
- Modify: `alpha_lab_service/tests/test_end_to_end.py`
- Modify: `alpha_lab_service/tests/test_manifest.py`
- Modify: `alpha_lab_service/protocol-manifest-v3.json`
- Modify: `alpha_lab_service/protected-files.json`
- Modify: `.github/CODEOWNERS`
- Modify: `.github/workflows/alpha-lab-python-service.yml`

**Interfaces:**
- Adds MCP tools `submit_factor_v2`, `run_factor_v2`, and `get_task_v2`; existing v1 tool names and output schemas remain unchanged.
- Trusted factory constructs v2 from server-owned Campaign registry, DatasetBuilder, immutable Worker identity, evaluator adapter, BrokerClientV2, and separate Store; no caller-selected factory/profile is accepted.
- Versioned governance manifest includes all new v2 service, worker, schema, tests, and acceptance artifacts. Frozen v1.1 manifest bytes remain identical.

- [x] **Step 1: Write failing tool/factory tests** for exact v2 tool schemas, v1 route parity, missing trusted v2 dependencies fail closed, and caller cannot select a factory, Store, image, or limits.
- [x] **Step 2: Run** `pytest alpha_lab_service/tests/test_mcp_v2_tools.py alpha_lab_service/tests/test_mcp_tools.py -q`; confirmed v2 tools were absent before wiring.
- [x] **Step 3: Implement** additive MCP wiring, trusted factory creation, governance hashes/required paths, CODEOWNERS and CI coverage without rewriting frozen v1.1 artifacts. Existing broad CODEOWNERS/CI patterns already covered v2; added assertions for the v3 manifest path.
- [x] **Step 4: Run** focused MCP, manifest, CI protection, full alpha_lab_service test suite, and check frozen v1.1 manifest hash against the recorded acceptance value.
- [x] **Step 5: Commit** as `feat(alpha-lab): expose factor service v2 tools`.

### Task 8: Release acceptance, actual-container boundaries, and documentation

**Files:**
- Create: `alpha_lab_service/tests/test_v2_container.py`
- Create: `docs/alpha_lab/python-factor-service-v2-acceptance.md`
- Create: `docs/alpha_lab/python-factor-execution-protocol-v2.md`
- Modify: `docs/alpha_lab/python-factor-service-acceptance.md`

**Interfaces:**
- Acceptance runner uses a trusted Campaign and real one-month BTC 5m dataset, target 8,928 as-of evaluations (or the exact authorized calendar count), and reports resolved manifest/fingerprint, chunk plan, hashes, runtime, and final descriptive Evidence.
- Container tests exercise actual pinned Worker image for disabled network, unavailable Docker socket, protected host path/data nonaccess, memory/CPU/PID/tmp/time limits, malformed frames, and missing runtime behavior.

- [x] **Step 1: Add failing acceptance assertions** for complete 5m month across chunks, stateless and stateful sequential parity, forced restart, no partial Evidence, actual-container denial tests, v1 behavior, and formal inference `BLOCKED`.
- [x] **Step 2: Run** v2 service unit/integration tests and actual-container acceptance against the committed immutable digest.
- [x] **Step 3: Implement** concise protocol and acceptance docs with exact commands, identities, limits, and results; keep formal inference status explicitly blocked.
- [x] **Step 4: Run** the complete Alpha Lab service/contracts acceptance suite, repeat the real BTC month, compare output/checkpoint hashes across restart, and verify the frozen v1.1 bytes.
- [x] **Step 5: Commit** as `docs(alpha-lab): record factor service v2 acceptance`.

## Plan self-review

- **Spec coverage:** API/identity: Tasks 1–2, typed PIT inputs: Tasks 1–3, chunking and deterministic merge: Task 3, bounded state: Task 4, persistence/retry/atomic Evidence: Tasks 5–6, Broker isolation: Tasks 4 and 8, v1 compatibility/governance: Task 7, acceptance and inference block: Task 8.
- **Step scan:** Every task has named failing tests, a targeted command, exact interfaces or configuration, implementation scope, and an independent commit.
- **Type consistency:** `AuthorizedFactorRunV2` is the shared trusted context; chunk plan and chunk frame builders consume it; scheduler calls fixed Broker v2 and `ResearchStoreV2`; evaluator receives only complete merged results.
- **Review focus:** Each of the five listed adversarial or recovery cases is assigned to a named test module and owning task.
- **Proportion:** Eight reviewable deliverables keep protocol, authorization, scheduling, Worker, persistence, orchestration, integration, and release acceptance separated while sharing explicit typed interfaces.

## Deferred release gates

- Remote GitHub Code Owner enforcement and branch protection require repository-side evidence; local manifest/CI checks alone do not prove remote enforcement.
- General Agent use stays blocked until actual-container isolation and host data/Store separation are evidenced.
- PR-06 unified TS/CS reporting and formal inference qualification remain out of scope.
