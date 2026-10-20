# Alpha Lab MCP Python Factor Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a separate MCP Streamable HTTP service that evaluates Agent-authored Python factors only inside a resource-limited, no-network Worker and records descriptive results in a versioned independent Store.

**Architecture:** Keep the v1.1 evaluator and historical Store untouched. An authenticated MCP control plane accepts source bytes, a trusted scheduler streams point-in-time history through a narrow Broker, an immutable Worker image evaluates one as-of section at a time, and a trusted Evaluator writes append-only descriptive Evidence/Decision records.

**Tech Stack:** Python 3.11+, MCP SDK Streamable HTTP, Pydantic, SQLite, Docker-compatible isolated Worker, NumPy, Pandas, SciPy, PyWavelets, pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-alpha-lab-mcp-factor-service-design.md`

## Global Constraints

- Do not modify `alpha_lab/freeze-manifest-v1.1.json`, frozen v1.1 statistical/evaluation code, historical SQLite, historical Evidence/Decision, raw data, or live trading configuration.
- Never import, compile or execute submitted source in MCP Server, Scheduler, Broker or Evaluator processes.
- Do not treat AST checks, prompt rules, image existence or benign tests as a security boundary.
- Worker has no network, Docker socket, Lab source, labels, historical Store, holdout data, or host credentials.
- Worker dependencies are preinstalled and pinned; runtime installation is disabled.
- Official inference status remains `BLOCKED`; expose descriptive outputs only as `EXPLORATORY`/`DIAGNOSTIC`.
- Any security failure blocks execution; do not tune thresholds or weaken the container profile to obtain a pass.
- Do not change a CI failure without a reproducible failing run/log; record unavailable CI evidence explicitly.
- Preserve `alpha_lab/freeze-manifest-v1.1.json` byte-for-byte. Because it currently hashes `CODEOWNERS` and the existing Alpha Lab workflow, any necessary protection/trigger update must create a distinct versioned governance manifest and switch the active check without rewriting v1.1.

## Review Focus

- A client-supplied path or traversal string must never become a host path or mount; test submit requests with absolute paths, `..`, symlinks and unknown fields.
- A Worker can read only data available at the current `as_of`; test cutoff, truncation and future perturbation.
- A malicious factor must not read labels/core/store, alter another job, access network, or escape resource bounds; test in the actual container profile.
- Same source/config/data/runtime must produce byte-identical canonical output; test two clean independent worker executions.
- Formal inference remains blocked even when descriptive metrics look strong; assert no p-value qualification and no `SUPPORTED` conclusion is emitted.

---

### Task 1: Freeze the execution protocol and service manifest boundary

**Files:**
- Create: `alpha_lab_service/__init__.py`
- Create: `alpha_lab_service/protocol.py`
- Create: `alpha_lab_service/manifest.py`
- Create: `alpha_lab_service/protocol-manifest-v1.json`
- Create: `alpha_lab/freeze-manifest-v1.2.json`
- Test: `alpha_lab_service/tests/test_protocol.py`
- Test: `alpha_lab_service/tests/test_manifest.py`

**Interfaces:**
- `FactorRequest` validates source text, hypothesis, fixed window, declared symbols, timeframe, parameters, and bounded limits; it has no filesystem/image/command fields.
- `FactorOutput` contains one timestamp, declared-symbol keyed finite values, bounded diagnostics and protocol version.
- `canonical_request_hash(request, *, data_fingerprint, image_digest, engine_identity) -> str` hashes canonical source/config/runtime identity.
- The service manifest covers its verifier, protocol, service files, CI, Worker build context, lock files, admission script and Code Owner boundary. The separate v1.2 governance manifest preserves v1.1 unchanged and records any changed owner/workflow boundary.

- [ ] **Step 1: Write failing request-contract tests** for accepted source bytes, exact required fields, maximum source size, scalar parameter types, forbidden path/command/image fields, and stable canonical identity.
- [ ] **Step 2: Run the protocol tests** with `/home/von/workspace/venv/bin/python -m pytest -q alpha_lab_service/tests/test_protocol.py`; expected failure is missing protocol types/functions.
- [ ] **Step 3: Implement protocol models and canonical identity** in `protocol.py`; reject NaN/Inf parameters, unknown keys, invalid time bounds and oversized source before queueing.
- [ ] **Step 4: Write and run manifest tests** proving changed/untracked service, Worker, lock, workflow, admission verifier, or Code Owner boundary files fail the appropriate new manifest while the v1.1 manifest remains byte-identical.
- [ ] **Step 5: Implement the isolated service manifest verifier** and verify the initial manifest using `python -m alpha_lab_service.manifest`.

### Task 2: Build the pinned untrusted Factor Worker

**Files:**
- Create: `containers/alpha-lab-factor-worker/Dockerfile`
- Create: `containers/alpha-lab-factor-worker/worker.py`
- Create: `containers/alpha-lab-factor-worker/requirements.lock`
- Create: `containers/alpha-lab-factor-worker/seccomp.json`
- Create: `containers/alpha-lab-factor-worker/profile.json`
- Create: `alpha_lab_service/worker_profile.py`
- Test: `alpha_lab_service/tests/test_worker_contract.py`
- Test: `alpha_lab_service/tests/test_worker_container.py`

**Interfaces:**
- Worker stdin accepts an `init` frame `{protocol_version, source, symbols, params, seed}`, then sequential `step` frames `{as_of, history}`, and finally `end`; it emits a ready frame and one response `{as_of, values, diagnostics, output_hash}` per step.
- Worker source loading occurs only inside the container; the Worker harness accepts only `compute_factor(history, *, as_of, params)`.
- `WorkerProfile` in `worker_profile.py` fixes image digest, network, mounts, UID, capabilities, read-only root, pids, memory, CPU, tmp size and timeout; the Broker consumes this immutable profile. Source/data use framed standard streams; no host source or data path is mounted.

- [ ] **Step 1: Write failing contract tests** for required function/signature, UTC sorted input, one output value per declared symbol, finite numeric values, malformed output and diagnostic size.
- [ ] **Step 2: Run tests and confirm expected missing-worker failures.**
- [ ] **Step 3: Implement Worker entrypoint** that loads source only in worker, seeds supported RNGs, invokes one as-of request, canonicalizes the result and returns no data other than factor values/limited diagnostics.
- [ ] **Step 4: Lock exact base image and NumPy/Pandas/SciPy/PyWavelets versions**; build once, record resulting image digest, and verify the built image contains no installer step at runtime.
- [ ] **Step 5: Write actual-container negative tests** for network, Docker socket, protected path reads/writes, label/holdout/store/core visibility, subprocess/pid exhaustion, memory exhaustion, timeout and cross-task interference.
- [ ] **Step 6: Run Worker tests through the target Docker daemon.** Expected pass requires each forbidden action denied/contained, bounded resource termination, and no skipped security assertion. If the daemon/profile cannot run, stop with `EXECUTION_BLOCKED`.

### Task 3: Implement trusted as-of data feed and evaluator adapter

**Files:**
- Create: `alpha_lab_service/data_feed.py`
- Create: `alpha_lab_service/evaluator.py`
- Test: `alpha_lab_service/tests/test_causality.py`
- Test: `alpha_lab_service/tests/test_evaluator.py`

**Interfaces:**
- `build_asof_frame(panel, *, as_of, symbols, required_columns) -> DataFrame` includes only rows with `available_time <= as_of`, stable UTC `(open_time, symbol)` order, and no labels.
- `evaluate_factor_output(outputs, labels, *, baseline, identity) -> dict` is trusted code; uses frozen point-metric/evaluation implementations without modifying them and emits only the descriptive projection.

- [ ] **Step 1: Write failing feed tests** for unavailable rows, cutoff equality, future timestamps, missing symbols, shifted cross-asset bars, duplicate rows and absent label columns.
- [ ] **Step 2: Run the causality test module and verify expected failures.**
- [ ] **Step 3: Implement the bounded feed** over synthetic MarketPanel inputs; never pass a full future panel or label data to the Worker.
- [ ] **Step 4: Add truncation and future-perturbation tests** that compare each as-of output under shorter history and modified future-only trusted data.
- [ ] **Step 5: Write failing evaluator tests** for IC, RankIC, stability, incremental comparison, missing-output handling, descriptive status tags and suppression of inferential claims.
- [ ] **Step 6: Implement the trusted adapter** using existing frozen functions; filter p-values/qualification fields and prevent `SUPPORTED` conclusions at the service boundary.
- [ ] **Step 7: Run feed/evaluator tests** and confirm labels are never in serialized Worker input, environment, mounts or diagnostics.

### Task 4: Create the independent append-only task/Evidence/Decision Store

**Files:**
- Create: `alpha_lab_service/store.py`
- Test: `alpha_lab_service/tests/test_store.py`

**Interfaces:**
- Store path: `user_data/research/alpha_lab/stores/python-factor-v1/experiments.sqlite3`.
- `ResearchStoreV1(path)` refuses the historic DB, prior v1.1 Store, symlink/same-file aliases, and mismatched store identity; it never migrates an existing unrelated DB.
- `append_task`, `append_evidence`, `append_decision`, `get_task`, and `list_history` use canonical identities and immutable Evidence/Decision semantics.

- [ ] **Step 1: Write failing schema/identity tests** for new path creation, identity mismatch, legacy path, symlink/hardlink aliases, duplicate task reuse, conflicting Evidence rejection, missing-run Decision rejection, and read-only SQLite URI write rejection.
- [ ] **Step 2: Run Store tests and confirm expected missing-store behavior.**
- [ ] **Step 3: Implement versioned schema and identity checks** without importing or changing `alpha_lab.research_store` internals; expose a read-only URI for history queries.
- [ ] **Step 4: Add disposable-copy UPDATE/DELETE attempts** for every immutable table and assert SQLite rejects the operations; trigger names alone are not evidence.
- [ ] **Step 5: Verify reopen stability, `integrity_check`, `foreign_key_check`, Store hash, and unchanged historical/v1.1 Store hashes.**

### Task 5: Implement MCP Streamable HTTP server and narrow execution Broker

**Files:**
- Create: `alpha_lab_service/server.py`
- Create: `alpha_lab_service/broker_server.py`
- Create: `alpha_lab_service/broker_client.py`
- Create: `alpha_lab_service/jobs.py`
- Create: `alpha_lab_service/auth.py`
- Test: `alpha_lab_service/tests/test_mcp_tools.py`
- Test: `alpha_lab_service/tests/test_broker.py`

**Interfaces:**
- MCP tools: `submit_factor`, `run_factor`, `get_task`, `get_history`, `append_decision`.
- Broker interface: `start_worker(task_id: str, init_frame: bytes) -> WorkerSession`, `step_worker(session, frame: bytes) -> WorkerResult`, `stop_worker(session) -> None`; Broker is a separate process and selects only fixed image/command/profile. The MCP Server has no runtime socket.
- Server listener requires auth and accepts no file paths, host paths, image names, commands or arbitrary worker arguments.

- [ ] **Step 1: Write failing MCP tests** for Streamable HTTP registration, authentication, tool input validation, source bytes handling, idempotent submit, bounded queue, sanitized errors, and proof the MCP process does not import/execute submitted source.
- [ ] **Step 2: Run tests and confirm missing tools/server failures.**
- [ ] **Step 3: Implement MCP server** using the pinned SDK; keep server process unable to import source and without runtime socket access.
- [ ] **Step 4: Write failing Broker tests** for fixed image/command/mounts, timeout, cancellation, output size, resource limit and unavailable runtime behavior.
- [ ] **Step 5: Implement Broker with fixed server-owned input references**; reject any client-influenced paths/mounts and return typed fail-closed task status.
- [ ] **Step 6: Verify one task per request and isolation between concurrent tasks** under the configured bounded concurrency.

### Task 6: End-to-end synthetic acceptance and reproducibility

**Files:**
- Create: `alpha_lab_service/tests/fixtures/factors/momentum.py`
- Create: `alpha_lab_service/tests/fixtures/factors/fft_window.py`
- Create: `alpha_lab_service/tests/fixtures/factors/malicious.py`
- Create: `alpha_lab_service/tests/test_end_to_end.py`
- Create: `docs/alpha_lab/python-factor-execution-protocol-v1.md`
- Create: `docs/alpha_lab/python-factor-service-acceptance.md`

- [ ] **Step 1: Write failing end-to-end tests** that submit each fixture by source content (never file path) using synthetic 2022–2024-like data.
- [ ] **Step 2: Verify momentum and FFT/wavelet factors** produce aligned causal values, descriptive IC/RankIC/stability/incremental outputs and `EXPLORATORY`/`DIAGNOSTIC` status only.
- [ ] **Step 3: Verify the malicious fixture** attempts filesystem escape, network, protected-resource access, process/memory exhaustion and cross-task interference; every attempt must be denied or contained.
- [ ] **Step 4: Run two clean executions** with identical source/config/data/image/evaluator identity and assert byte-identical canonical output hashes and one idempotent Evidence identity.
- [ ] **Step 5: Verify invalid or non-reproducible tasks** fail closed and cannot write Evidence or Decision; verify Decision is bound to the Evidence digest.
- [ ] **Step 6: Run all service-focused tests and actual-container security suite.** Do not run real factor research.

### Task 7: Lock environment, protect admission code, and CI the service

**Files:**
- Create: `alpha_lab_service/pyproject.toml`
- Create: `alpha_lab_service/uv.lock`
- Create: `.github/workflows/alpha-lab-python-service.yml`
- Modify: `.github/CODEOWNERS`
- Modify: `.github/workflows/alpha-lab-freeze.yml`
- Create: `alpha_lab/freeze-manifest-v1.2.json`
- Create: `alpha_lab_service/protected-files.json`
- Test: `alpha_lab_service/tests/test_ci_protection.py`

- [ ] **Step 1: Write failing protection tests** proving owner and CI coverage includes service, Worker, protocol, lock, admission verifier, both workflows, Codeowners and new governance manifest; prove the service manifest includes the verifier and admission implementation.
- [ ] **Step 2: Capture any known CI failure run URL/job/log.** Reproduce the failure before editing; if no record is available, document the unavailable evidence and do not invent a repair.
- [ ] **Step 3: Add exact MCP/Evaluator and Worker dependency locks** and verify locked sync/build works without runtime network access.
- [ ] **Step 4: Add dedicated CI workflow** that runs lint, protocol/unit tests, synthetic E2E, new governance-manifest verification, service-manifest verification and actual container security tests; runtime unavailability fails the security job.
- [ ] **Step 5: Add Code Owner rules** for the admission script, service/Worker files, both workflows and manifests; add these paths to CI triggers. Preserve the old v1.1 manifest and create a distinct governance baseline for the changed boundary.
- [ ] **Step 6: Run exact CI commands locally where supported** and report any environment-dependent security job as blocked, not skipped-pass.

### Task 8: Final release gate and handoff

**Files:**
- Modify: `docs/alpha_lab/python-factor-service-acceptance.md`
- Modify: `alpha_lab_service/protocol-manifest-v1.json`

- [ ] **Step 1: Run the full Phase 1 verification set** after all implementation changes; capture commands and output, including all failure tests and actual container profile.
- [ ] **Step 2: Verify untouched boundaries** by hashing v1.1 freeze manifest, historical SQLite, prior v1.1 Store, raw market data, and live configuration before/after.
- [ ] **Step 3: Regenerate only the new service and governance manifests** after final file sets are known; do not write the v1.1 manifest.
- [ ] **Step 4: Run fresh verification against both new manifests** and ensure no untracked protected file is omitted.
- [ ] **Step 5: Mark service `BLOCKED` if any required security gate, container test, causality test, CI protection test, Store integrity test or repeatability test is incomplete; never claim autonomous research is enabled.**
- [ ] **Step 6: Report changed files, tests, security limitations, confirmed CI issue status and the next Maintainer-controlled step; stop after Phase 1.**

