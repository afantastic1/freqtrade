# Python Factor Research Service — Phase 1 acceptance

## Current state

**Service status: `BLOCKED` for general Agent use.** The checked-in service
surface and local Worker profile pass the synthetic acceptance described below,
but a configured deployment has not yet proven that the MCP account cannot
access the Docker socket or protected Lab/data/SQLite paths. No OpenCode/Codex
host permission boundary is established by this repository. Do not use prompt
instructions as evidence of isolation.

No real market factor research was run. The end-to-end scenarios use only
synthetic five-asset data with timestamps spanning 2022–2024-like development
periods. No formal inference, trading integration, or 2025+ holdout access is
enabled.

## Acceptance evidence

Final local acceptance passed with 50 tests (including the actual Docker Worker negatives and synthetic end-to-end scenarios); the run emitted three non-failing pytest/Pydantic configuration warnings. It used Worker image ID
`sha256:776b65dc3f155eabcdd0b4eb16b2faf3bf1eb5d74d54c321bbb0b95e3cf8baa0`
(rebuild and record a new digest whenever Worker source or build inputs change).
Tests cover:

- strict protocol requests, canonical identity and manifests;
- synchronized as-of inputs, availability/open-time cutoffs, label-column
  rejection, truncation consistency, and future-only data perturbation;
- fixed MCP tools, bearer authentication, idempotent source submission and
  direct evidence/decision status restrictions;
- an independent versioned Store, foreign-key behavior, immutable triggers,
  conflict rejection and read-only query mode;
- momentum and FFT/wavelet source content in clean independent containers,
  descriptive IC/RankIC, stability and incremental summaries, and stable output
  hashes;
- malicious network and protected-path attempts, unprivileged execution,
  seccomp/AppArmor state, memory/PID/time/output limits, and task scratch
  isolation.

Reproduce with the locked service environment and a locally built Worker image:

```bash
docker build --pull=false \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple \
  -t alpha-lab-factor-worker:local containers/alpha-lab-factor-worker
export ALPHA_LAB_WORKER_IMAGE="$(docker image inspect --format '{{.Id}}' alpha-lab-factor-worker:local)"
uv sync --project alpha_lab_service --locked --group test
uv run --project alpha_lab_service --locked python -m pytest -q alpha_lab_service/tests
uv run --project alpha_lab_service --locked python -m alpha_lab_service.governance
```

The actual-container test suite fails if Docker or the configured immutable
image is unavailable. It is not a skipped-pass suite.

## Required before unblocking

1. Deploy MCP and Broker under distinct OS identities. Prove the MCP identity
   cannot read/use the Docker socket and cannot read or modify the frozen core,
   market data, holdout or either historical SQLite database. Prove the Broker
   exposes only its fixed authenticated operation.
2. Configure a trusted service factory that binds the intended 2022–2024
   development panel and trusted labels; verify data fingerprints and storage
   permissions on the target host. Keep the Worker feed free of labels/future
   rows.
3. Run the new locked CI and v1.2 governance verifier on the target branch, then
   verify repository branch protection requires those checks and Code Owner
   review. No remote CI run or branch-protection setting is asserted by this
   local report.
4. Rebuild the Worker from the committed Dockerfile and exact locks, rerun all
   container negatives on the deployment daemon, record its new image digest,
   and regenerate/verify the versioned manifests.

Until each item is evidenced, keep the general research service `BLOCKED`.
