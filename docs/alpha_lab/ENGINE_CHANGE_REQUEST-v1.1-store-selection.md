# ENGINE_CHANGE_REQUEST: v1.1 versioned Store selection

**Status: PENDING MAINTAINER / CODEOWNER REVIEW — no frozen-core change made.**

## Scope

Allow the trusted cross-sectional Runner and Agent Gateway to explicitly select
the independent `alpha-lab-v1.1-cross-sectional-store` at
`user_data/research/alpha_lab/stores/v1.1-cross-sectional/experiments.sqlite3`.
Keep legacy time-series Research Store selection explicitly separate. Neither
path may fall back to the other. Resolve canonical paths, reject symlinks and
same-file aliases to the historic `experiments.sqlite3`, and verify the
singleton store identity/version before opening for writes.

## Evidence motivating the change

- Current `cross_sectional_runner.py` derives its DB as `root / "experiments.sqlite3"`.
- Current `research_agent_cli.py` uses the same legacy path for history, decisions,
  family review, and Runner execution.
- A new independent empty v1.1 CS store exists and its synthetic schema/conformance
  acceptance passes; historic DB hash remains unchanged.
- The current Admission Gate only checks for table presence and trigger SQL text.
  That is not sufficient evidence of append-only behavior; the independent
  conformance harness performs UPDATE/DELETE attempts against a disposable copy.

## Required version and regression scope

Before implementation, Maintainer assigns the next engine/protocol version and
approves this request. Tests must fail before the change and then prove:

1. Gateway and Runner select the v1.1 store only for CS actions.
2. Legacy actions continue selecting only the legacy DB.
3. Default/missing/invalid store identity fails closed; no fallback or implicit
   initialization of a different existing DB occurs.
4. Absolute paths, `..`, symlinks, hardlink/same-file aliasing, and direct historic
   DB path are rejected for the v1.1 role.
5. Evidence/Decision FK, idempotence, conflict, actual mutation rejection,
   read-after-reopen, and read-only sanitized queries pass on disposable fixtures.
6. A Gateway/Runner synthetic request writes only to a disposable v1.1 DB; historic
   DB pre/post hashes match.

Then run Alpha Lab full regression, SQLite integrity/concurrency checks, freeze
manifest generation for the new version without replacing the v1.0 or prior v1.1
manifest, Golden comparison, and CODEOWNER review on the protected branch.

## Stop conditions

Reject the migration if store identity can be bypassed, historic path can be
selected, any mutation test succeeds, legacy query compatibility is lost, the
historic hash changes, or the new version manifest/CI is not reviewed. Until
approved and verified, current Gateway/Runner remain bound to their existing
path and CS deployment admission remains blocked.
