# Python Factor Execution Protocol v2.0

## Boundary and request

The v2 MCP tools accept a source declaration tied to a trusted
`research_request_id`. The service resolves that ID through the configured
ResearchRequest registry and revalidates its Campaign policy before creating or
resuming work. Symbols, dates, split, source adapters, availability rules,
labels, Store, image, limits and host paths are not tool parameters.

The trusted DatasetBuilder produces two separate products. Only its typed,
point-in-time `WorkerInputV2` rows are serialized to the Worker. Evaluator
labels, evaluator objects and host paths remain in the control plane. The
time-series request has one trusted symbol; the control plane restores that
symbol when the DatasetBuilder's scoped product omits a redundant symbol
column.

## Frames

Each Broker request contains only `task_id`, `chunk_id` and canonical frames.
The fixed v2 endpoint is `/v2/run-chunk`. A request contains:

1. `init`: protocol and plan identities, source content, scalar parameters,
   authorized symbols, registered input schema, execution mode and (for
   stateful mode) the server-registered state schema and prior committed state.
2. `warmup`: timestamp, typed history and `initialize` or `read_only` phase.
3. One to 512 `step` frames, each with an exact UTC decision timestamp and
   available history.
4. `end`.

Every frame is schema checked by the Broker and Worker. The Worker emits a
`ready` frame followed by one finite numeric result per authorized symbol and
decision timestamp. A stateful request ends with one bounded `checkpoint`
frame. The control plane validates exact order, timestamps, symbols, plan hash
and state schema before persisting a chunk.

The v2 Worker source defines `compute_factor(history, *, as_of, params)` in
stateless mode. Stateful source may also define
`initialize_state(warmup, params)` and must return `(values, next_state)` from
`compute_factor(..., state)`. Warmup initializes state once. A continuation
receives the previous committed checkpoint and read-only warmup; a retry or
restart never starts from an uncommitted checkpoint.

## Fixed limits

| Budget | Service limit |
| --- | ---: |
| Evaluation steps per Worker | 512 |
| Total evaluation steps | 100,000 |
| Serialized input per Worker | 16 MiB |
| Cumulative serialized input, including retries | 512 MiB |
| Serialized output per Worker | 4 MiB |
| Cumulative committed output and checkpoints | 256 MiB |
| Canonical state | 1 MiB |
| Cumulative Worker wall time | 1,800 seconds |
| Attempts per chunk | 3 |

The budgets are immutable server configuration. Chunk input and output hashes,
attempts, committed values and state are persisted in the private v2 Store.
After process loss, an orphaned attempt is marked incomplete and may be
replayed from the prior committed checkpoint. Its unknown duration is charged
conservatively from attempt start through recovery.

## Evidence

The evaluator runs only after all planned chunks are committed and the merged
factor index exactly matches the authorized schedule. Evidence publication and
the `COMPLETED` transition share one Store transaction. Partial output never
produces Evidence. Results are `EXPLORATORY` or `DIAGNOSTIC`; formal inference
is `BLOCKED` and p-values or `SUPPORTED` conclusions are not exposed.

## Runtime profile

The committed Worker profile pins image
`sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced`,
disables networking and host mounts, drops all capabilities, runs as UID/GID
`65532:65532`, and uses a read-only root, 512 MiB memory, one CPU, 64 PIDs,
16 MiB no-exec `/tmp`, Docker's default seccomp profile and a 60 second
per-container timeout. The Broker accepts no image or profile override from a
factor request.

Run the local v2 contract and container checks with:

```bash
ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced \
  python -m pytest alpha_lab_service/tests/test_worker_v2_contract.py \
  alpha_lab_service/tests/test_v2_container.py -q
```

The actual-container tests require Docker and the pinned local image. A missing
image or runtime is an execution block, not a passing skipped test.
