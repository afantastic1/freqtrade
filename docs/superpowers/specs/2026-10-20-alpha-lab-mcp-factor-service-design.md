# Alpha Lab MCP Python Factor Research Service — Design

**Status:** Design draft for review  
**Scope:** Phase 1 service and isolated factor execution only  
**Repository baseline:** `codex/fundamental-data-adapter`, inspected HEAD `b567c1b1`  
**Release posture:** Exploratory/diagnostic only; formal inference remains `BLOCKED`

## 1. Intent and constraints

Create an independently deployable MCP research service that accepts Agent-authored
Python factor source while preserving the frozen Alpha Lab v1.1 evaluator and data
contracts. The service separates source creation, request handling, untrusted
execution, and trusted evaluation. It must never expose a trading action.

The service is a distinct execution protocol and Store version. It does not alter
`alpha_lab/freeze-manifest-v1.1.json`, v1.1 statistical methods, historical SQLite,
historical Evidence/Decision rows, market source files, or live-trading settings.
No output from this service may be interpreted as a formal `SUPPORTED` conclusion.

## 2. Repository findings that shape the design

- `alpha_lab/factors/candidate_loader.py` imports candidate source into the caller
  process. The legacy candidate flow is therefore not a safe Worker and is never
  called by this service.
- `alpha_lab/research_agent_cli.py` is a fixed-action gateway and currently uses
  the legacy `experiments.sqlite3`; it is not extended to run arbitrary Python.
- The v1.1 frozen manifest verifies, but covers Alpha Lab files rather than a new
  independent service package. The existing admission script is tracked in Git
  but is not included in that manifest, and its CI path coverage is incomplete.
- The Alpha Lab workflow installs the broad project/dev environment through pip;
  it does not use `uv.lock` for its install. The repository lock does not contain
  the MCP SDK or PyWavelets. The inspected environment had MCP, NumPy, Pandas and
  SciPy available; PyWavelets was absent.
- Existing isolation documentation says the actual Agent process is not forced
  inside the previously tested worker. Docker reports AppArmor, seccomp and cgroup
  namespace support, but runtime availability alone is not an isolation test.
- No specific failing CI workflow run or log was available in the local checkout.
  The implementation phase must use a concrete failure record if a current CI
  failure is to be repaired; speculative unrelated changes are out of scope.

## 3. Component boundaries

```text
Agent
  │ source bytes + declarative request (no client filesystem paths)
  ▼
MCP Service (Streamable HTTP, unprivileged, authenticated)
  │ bounded task request
  ├──────────────► Trusted Scheduler / Data Feed
  │                  ├─ owns bounded development data and trusted labels
  │                  ├─ sends only available history through the as-of cutoff
  │                  └─ retains labels and 2025+ data outside Worker mounts
  ▼
Execution Broker (narrow fixed operation; no arbitrary mounts/commands)
  │ fixed image digest, fixed resource profile, no-network worker
  ▼
Factor Worker (untrusted Python + pinned NumPy/Pandas/SciPy/PyWavelets)
  │ one scalar per asset at the current as-of time + bounded diagnostics
  ▼
Trusted Evaluator ──► versioned append-only Python-factor Store
```

The MCP process and Worker are separate trust domains. The MCP process never
imports, compiles or executes submitted code. The Worker receives neither the Lab
core source, evaluator, database, raw data directories, labels nor host credentials.
The Worker has no Docker socket, network, host PID namespace, writable root
filesystem, or host-sensitive mounts. Only a fixed Worker image and per-task
read-only input plus bounded output scratch are available.

If the runtime cannot enforce the required profile or negative tests fail, the
service refuses execution and reports `EXECUTION_BLOCKED`. Prompt instructions,
AST filters, container presence, and successful benign runs are not isolation
proofs.

## 4. MCP surface and request protocol

Transport is MCP Streamable HTTP. The service is bound to a private listener and
requires deployment authentication. Tools are deliberately narrow:

| Tool | Purpose | Writes |
|---|---|---|
| `submit_factor` | Accept source string and a strict configuration; return immutable task/factor identity | New task request only |
| `run_factor` | Start one bounded execution for a submitted task | Task lifecycle and eventual Evidence |
| `get_task` | Read task state and sanitized diagnostics | None |
| `get_history` | Read sanitized prior task summaries from the new Store | None |
| `append_decision` | Append a human/Agent interpretation bound to exact Evidence identity | Append-only Decision |

Requests contain source text, hypothesis, fixed research-window/config values,
declared parameters and output limits. They do not contain source paths, panel
paths, database paths, shell commands, image names, mount points, or arbitrary
worker arguments. Source size, request size, maximum lookback, output dimensions,
time window, and job concurrency are bounded before scheduling. Syntax checking
is diagnostic only; it is not treated as a security control.

Submission and execution are separate operations. A canonical request hash makes
identical source/config/data/runtime submissions idempotent. Task states are
`SUBMITTED`, `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `TIMED_OUT`,
`RESOURCE_LIMITED`, and `EXECUTION_BLOCKED`. Failed tasks cannot mutate completed
Evidence or be silently retried under a different identity.

## 5. Factor Execution Protocol v1.0

The protocol is versioned independently from the frozen v1.1 evaluator. Its public
contract is:

```python
def compute_factor(history: pandas.DataFrame, *, as_of: pandas.Timestamp,
                   params: Mapping[str, int | float | str | bool]) -> pandas.Series:
    ...
```

`history` has a sorted, unique UTC `(open_time, symbol)` index and only approved
OHLCV-derived columns plus availability metadata. Each call receives records whose
`available_time <= as_of`; the result is one finite numeric value per declared
symbol for the current `as_of` section, indexed by symbol. There is no label,
future return, evaluator object, filesystem path, or service handle in the contract.
Parameters are canonical JSON scalars within declared bounds. The protocol fixes
missing-value, duplicate-index, non-finite-output, symbol-set, ordering and
diagnostic-size behavior.

The trusted scheduler advances `as_of` monotonically and does not send the next
section until the Worker has returned the current section. Each factor invocation
therefore has no future rows available to inspect. A shared asset/time grid and
explicit alignment checks prevent cross-asset look-ahead. The evaluator computes
future labels only outside the Worker after factor output has been aligned.

## 6. Causality and evaluation

The first phase verifies:

1. **Availability cutoff:** all input records are available by `as_of`; unavailable
   or future records are rejected before delivery.
2. **Truncation consistency:** recomputing the same output from a shorter history
   ending at `as_of` agrees with the corresponding output from the normal feed.
3. **Future perturbation:** changing all trusted-side future rows after `as_of`
   does not change the Worker output at or before `as_of`.
4. **Label separation:** labels exist only in the Evaluator process and are absent
   from Worker inputs, mounts, environment and output channels.
5. **Cross-asset alignment:** each section has the declared synchronized symbol
   index; missing or shifted assets fail closed rather than being silently joined.

These tests demonstrate the service data boundary for tested requests; they do not
prove that arbitrary code is mathematically causal for every possible dataset.
The strong control is that future observations and labels are not delivered.

The trusted Evaluator owns labels, IC, RankIC, stability and incremental
comparisons. It uses frozen implementation without changing thresholds or formulas.
The service emits a descriptive projection only: point estimates, coverage and
stability summaries. Formal p-values, confidence-based qualifications, and
`SUPPORTED` verdicts remain unavailable while the registered method status is
`BLOCKED`. The result is tagged `EXPLORATORY` or `DIAGNOSTIC`.

## 7. Reproducibility and storage identity

Every run identity includes:

- factor source SHA-256 and canonical parameters/configuration;
- protocol and service version;
- fixed research window, universe, timeframe and split;
- bounded-data content fingerprint and availability semantics;
- Worker image digest and dependency lock hash;
- frozen Evaluator/engine identity and statistical registry/method status;
- deterministic seed and output contract version.

The Store is a new versioned SQLite path for this protocol, separate from both
the historical Research Store and the independently created v1.1 CS Store. It
contains explicit store identity/version metadata and append-only task, Evidence
and Decision records. Reuse requires matching full identity and output digest.
Evidence rows are immutable; changed source, config, data, image, evaluator or
protocol creates a different identity. A second independent execution must match
the result digest for the same identity or be marked non-reproducible and blocked.

## 8. Worker profile and operational limits

The pinned image preinstalls exact NumPy, Pandas, SciPy and PyWavelets versions;
no runtime package installation or network resolution is permitted. The exact
digest is recorded in run identity. The Worker runs as a dedicated unprivileged
UID with dropped capabilities, `no-new-privileges`, seccomp/AppArmor profile,
read-only root, no network, bounded temporary storage, memory/CPU/PID limits and
wall-clock timeout. It receives only a fixed protocol entrypoint, source bytes,
one as-of data message at a time, and a bounded output channel.

The MCP Server has no container runtime socket. A narrowly scoped Broker accepts
only a task ID and server-owned immutable input references; it selects a fixed
image digest and mount profile. It cannot be instructed by the Agent to mount an
arbitrary host path or run a shell command. Broker availability and actual denial
behavior must be demonstrated on the target host before the Worker gate can pass.

## 9. CI, dependency and protection plan

The new service has its own exact dependency lock and container build inputs. A
dedicated CI workflow triggers for service, Worker image, protocol, tests,
dependency lock, admission checker and workflow changes. It runs contract tests,
causal tests, repeatability tests and container negative tests where the runner
supports the required runtime. Missing runtime capability is a failed security
gate, not a skipped pass.

The existing v1.1 Manifest is not rewritten. A separate service/protocol manifest
covers the new service code, Worker build files, dependency lock, protocol,
Admission Gate implementation and relevant CI. The manifest verifier itself and
the admission script are included in a protected hash boundary. Changes to
frozen v1.1 code are excluded from this phase; any discovered need becomes a
separate reviewed engine-change request.

Before changing CI for a reported existing failure, record the failing run URL,
job and error. If the failure cannot be reproduced or the record is unavailable,
report that limitation instead of guessing at a fix.

## 10. Phase 1 deliverables and acceptance

Phase 1 implements the service protocol, MCP server, trusted feed/Evaluator
adapter, execution Broker contract, Worker image, independent Store, service
manifest/CI protection and focused tests. It does not enable autonomous research
or connect trading.

The acceptance suite uses synthetic development-like data and three submitted
sources:

- momentum: causal output and reproducible descriptive metrics;
- FFT or wavelet: causal windowed output, supported dependency, reproducible
  descriptive metrics;
- malicious: attempts to read/write outside task scratch, reach network, inspect
  labels/holdout/core/store, spawn uncontrolled processes or exceed resource
  limits. Each must be denied or contained without affecting the service, store,
  other tasks or host.

Acceptance also requires duplicate submission idempotence, immutable Evidence,
Decision binding, two-run output digest equality, no 2025+ data access, all five
causality checks, Store integrity and clean failure isolation. The service remains
`BLOCKED` until every required security condition passes on the actual runtime.

## 11. Non-goals

- No Alpha Lab v1.1 freeze-manifest edits or statistical-method changes.
- No inference calibration, threshold changes, formal `SUPPORTED` status or
  interpretation of p-values from the exploratory channel.
- No strategy backtest/trading integration, exchange access, 2025+ Holdout, new
  formal data sources, online dependency installation or arbitrary host commands.
- No cleanup, migration or rewrite of historical Evidence/Decision/SQLite.
- No unrestricted factor-execution support in the old `candidate_loader` path.
