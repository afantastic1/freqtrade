# Alpha Lab PR-08–10 Current-HEAD Acceptance Report

Date: 2026-10-21  
Branch: `my_dev`  
Code HEAD: `fef168b30f2fa6cbfc81e96ff43696dec00bb83b`  
Disposition: **PR-08 `BLOCKED`; PR-09 `BLOCKED`; PR-10 `BLOCKED_NOT_RUN`**

This report records the work executed against the current checkout. A `BLOCKED` result is an acceptance result, not a pass. Historical reports from earlier commits are context only. No historical Evidence, Research Decision, database, Campaign policy, frozen manifest, or source Feather file was modified. The Alpha Lab plan document and this report remain untracked; the BTC Catalog/normalized objects were written under `/tmp/alpha-lab-pr09/`.

## Decision summary

| PR | Result | Evidence completed | Open acceptance gate |
|---|---|---|---|
| PR-08 baseline audit | **BLOCKED** | Current HEAD/status; active and historical manifest checks; governance and statistical self-check; architecture/reuse inventory; full regression commands and evidence classes recorded | Current Python 3.13 suite has one reproducible Binance Vision timestamp-unit failure. The Alpha Lab service workflow's locked service-only environment fails collection because repository Freqtrade runtime dependencies are absent. No remote CI checks are associated with this SHA. |
| PR-09 data and deployment | **BLOCKED** | Current BTC perpetual 5m source maps to Catalog v3; 36 monthly partitions are `AVAILABLE`; 315,648 rows; coverage gateway returns `AVAILABLE` with zero missing intervals. A pinned Worker image was built and local container suite passed. | No fresh trusted deployment readiness for all eight controls; actual Agent runtime is not confined to that Worker; physical denial of raw/2025+ reads and production mounts/identities/stores is not verified. |
| PR-10 Agent real-data E2E | **BLOCKED_NOT_RUN** | Seven-tool profile contracts pass in service tests; actual Catalog coverage method returned `AVAILABLE` on real BTC partitions. | PR-09 dispatch gate is not satisfied. No live Agent MCP profile executed Request→Submit→Run→Status→Report→History; no Request, Worker run, Evidence ID, or real E2E log was created. |

## PR-08 — Current baseline audit

### Baseline and governance

- Code baseline: branch `my_dev`, HEAD `fef168b30f2fa6cbfc81e96ff43696dec00bb83b`.
- Starting worktree contained the untracked construction plan. The source edit explored while diagnosing a test failure was reverted; no tracked code or manifest differs from HEAD as a result of this task.
- `./.venv/bin/python -m alpha_lab.freeze --manifest alpha_lab/freeze-manifest-core-v1.2.json` — exit 0.
- `./.venv/bin/python -m alpha_lab_service.governance` — exit 0: v1.4 governance, service v3, active core v1.2, and historical core v1.1 valid.
- `./.venv/bin/python -m alpha_lab.stats.selfcheck` — exit 0.
- `./.venv/bin/python -m compileall -q alpha_lab alpha_lab_contracts data_engineering` — exit 0.
- Remote GitHub combined status for this SHA returned no status checks; the commit workflow-run lookup returned no PR workflow runs. Remote CI and branch-protection requirements are therefore **unverified**.

### Regression and CI findings

The current Python 3.13 environment uses pandas 3.0.6, which is permitted by the Data Engineering dependency range (`pandas>=2.2,<4`). The current full PR-08 regression command produced **652 passed, 1 failed in 41.86s**. The failing test is `tests/test_data_engineering_source_vision.py::test_download_verifies_checksum_caches_raw_zip_and_reads_month`: `parse_kline_zip` casts inferred `datetime64[ms]` timestamps directly to `int64`, then treats those millisecond values as nanoseconds; a valid 5-minute candle is rejected as misaligned. Re-running that exact test on the final source state reproduces the failure.

I verified locally that normalizing the datetime unit to nanoseconds makes the test pass and the broader command produce 653 passes. That code change also caused three service manifest/governance tests to reject the changed `data_engineering/sources/vision_archive.py` hash. I reverted the source change to preserve the protected service manifest and frozen-code boundary. The defect remains open for an owner-reviewed source and manifest change; the passing run with the temporary source edit is not acceptance evidence.

The service workflow `.github/workflows/alpha-lab-python-service.yml` syncs only `alpha_lab_service/pyproject.toml` before running tests. Replaying that locked service-only environment failed collection in six modules because `freqtrade.exchange` imports `cachetools`, which is a root Freqtrade dependency absent from the service lock. This environment failure is separate from product test failures. The Alpha Lab service suite then passed when run in a temporary combined environment containing the locked root development dependencies and locked service dependencies: **187 passed, 1 warning in 125.13s**, with Worker image `sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced`. The CI workflow setup was not changed because it is a protected governance path.

The root regression command was:

```bash
./.venv/bin/python -m pytest -o addopts='' -q \
  alpha_lab_contracts/tests/ alpha_lab/tests/ alpha_lab/calibration/ \
  tests/test_data_engineering.py tests/test_data_engineering_catalog_v3.py \
  tests/test_data_engineering_catalog_v3_cli.py tests/test_data_engineering_source_freqtrade.py \
  tests/test_data_engineering_source_pipeline.py tests/test_data_engineering_source_publish.py \
  tests/test_data_engineering_source_vision.py tests/test_data_engineering_source_cli.py \
  tests/test_data_engineering_sources_contracts.py tests/test_data_engineering_packaging.py \
  tests/test_cross_sectional_fundamental_runner.py tests/test_fundamental_asof.py \
  tests/test_fundamental_feature_bridge.py tests/test_fundamental_registries.py \
  tests/test_fundamentals.py tests/test_pit_audit.py tests/test_pit_evidence.py \
  data_engineering/scripts/test_fundamental_coverage_audit.py
```

The service workflow's service-only dependency setup is:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/alpha-lab-service-pr08-10-venv \
  uv sync --project alpha_lab_service --locked --group test
ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced \
  /tmp/alpha-lab-service-pr08-10-venv/bin/python -m pytest -q alpha_lab_service/tests
```

The latter failed at collection with `ModuleNotFoundError: No module named 'cachetools'`. The successful 187-test run used a combined temporary environment and is not represented as the workflow's exact clean-environment result.

The combined environment and passing service command are reproducible with:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/freqtrade-pr08-10-venv \
UV_CACHE_DIR=/tmp/freqtrade-pr08-10-uv-cache \
  uv sync --project . --python /usr/bin/python3.12 --locked --extra develop
uv export --project alpha_lab_service --locked --group test --format requirements-txt \
  --no-emit-project --output-file /tmp/alpha-lab-service-pr08-10-locked-requirements.txt
uv pip install --no-config --python /tmp/freqtrade-pr08-10-venv/bin/python \
  -r /tmp/alpha-lab-service-pr08-10-locked-requirements.txt
ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced \
  /tmp/freqtrade-pr08-10-venv/bin/python -m pytest -q alpha_lab_service/tests
```

### Existing capability and reuse audit

| Layer | Existing implementation | PR-08 disposition |
|---|---|---|
| Source discovery and immutable partitions | `data_engineering/sources/`, `data_engineering/catalog_v3.py` | Reuse the local Freqtrade Feather adapter and Catalog v3. Do not add a second catalog or alter source bytes. |
| Authorized request and temporal split | `alpha_lab_contracts/campaign_policy_v1.py`, `research_request_v2.py`, `alpha_lab/data/temporal_plan.py` | Reuse immutable Campaign Policy and ResearchRequest. Request bounds and split are trusted inputs, not Agent-selected file paths. |
| Dataset and PIT availability | `alpha_lab/data/source_resolver.py`, `dataset_builder.py`, `availability.py`, `build_products.py` | Reuse DatasetBuilderV2 and preserve worker/evaluator product separation. |
| Factor identity/execution | `alpha_lab/factors/registry.py`, service v2 request schemas and Worker protocol | Keep the engine registry distinct from experiment registry; no arbitrary Agent code or parallel service added by PR-08. |
| Evaluation and incrementality | `alpha_lab_service/evaluation_v2.py`, `alpha_lab/evaluation/incremental.py`, `incremental_stability.py` | Reuse descriptive evaluator and current diagnostic status. Formal inference remains blocked. |
| Task/Evidence/history | `alpha_lab_service/jobs_v2.py`, `store_v2.py`, `history_v2.py`, `agent_gateway_v2.py` | Reuse append-only service Store and existing history sources. Do not migrate or rewrite the legacy research database. |
| Agent surface | `alpha_lab_service/server.py::create_agent_mcp_server` and `agent_gateway_v2.py` | The restricted profile already exposes exactly seven tools; PR-10 should exercise it rather than create another profile. |

Current call chain:

```text
Freqtrade Feather / source adapter
  → Catalog v3 discovery, validation and immutable normalized partitions
  → DatasetSourceResolver + Campaign-bound ResearchRequest
  → DatasetBuilderV2 temporal plan and PIT availability
  → separate WorkerInput and Evaluator products
  → Agent MCP profile → AgentResearchGatewayV2
  → FactorServiceV2 / JobsV2 → Broker → isolated Worker
  → evaluation_v2 → ResearchStoreV2 Evidence → sanitized report/history
```

### Evidence class separation

| Evidence class | Current result | What it proves |
|---|---|---|
| Synthetic fixtures | **PASS** in the recorded test suites | Contract, authorization, failure, and deterministic behavior on fixtures only. |
| Local real data | **PASS for bounded BTC 5m Catalog coverage** | The identified local Feather bytes were sliced into verified Catalog v3 monthly partitions with the identities in [PR-09 coverage evidence](pr09-btc-coverage-evidence.json). |
| Local Docker Worker | **PASS for service container tests** | The pinned Worker image runs with the controls exercised by those tests. It does not show that an Agent process is forced to use this Worker. |
| Formal Agent deployment | **BLOCKED / NOT RUN** | No deployment-bound trusted readiness, MCP-to-worker execution, Request, or Evidence was observed. |

## PR-09 — Real BTC coverage and deployment readiness

The reusable `dataset_builder_acceptance` Campaign policy is version 1 with SHA-256 `b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f`. Its authorized Discovery interval is `[2022-01-01, 2025-01-01)`, and it permits BTCUSDT perpetual 5m OHLCV. No policy interval was widened and no Holdout was read for this acceptance.

The current source is `user_data/data/binance/futures/BTC_USDT_USDT-5m-futures.feather`, SHA-256 `f39767aefb7f9e2b6651912a1e84b85fef3ac6ada8a6026eeb153d93914d589c`. The fresh Catalog dataset ID is `binance-futures-btc-ohlcv-5m-6c7cb41c78f4`. The Catalog snapshot contains 36 monthly partitions, 315,648 rows, all partition quality states `AVAILABLE`, no missing monthly intervals, and a 300-second median/min/max cadence. The real `CatalogCoverageGatewayV2` call returned `status=AVAILABLE`, `observed_count=315648`, `missing_interval_count=0`, and 36 partition versions. Snapshot SHA-256: `1457dffa171fb3b4bf0007a352728ad588c29a50c1f2e118e1e46eb94598f39d`.

The Catalog and normalized data were built under `/tmp/alpha-lab-pr09/`. This proves the local mapping and requested interval coverage. The temporary Catalog is not configured as a trusted deployed source and is not a read-only deployment mount.

### Isolation controls

The Worker image was built from `containers/alpha-lab-factor-worker` and resolved to `sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced`. The Docker-backed service suite passed on the same image. The current checked-in `agent_isolation_attestation.json`, dated 2026-10-20, states that only an ephemeral negative-probe Worker was tested; `actual_agent_runtime_enforced=false`, `shell_spawn_denied=false`, and `symlink_escape_denied=false`. It is not fresh deployment readiness and cannot satisfy PR-09.

| Required check from `isolation_v2.py` | Current deployment evidence |
|---|---|
| `worker_image_immutable` | Local immutable image digest observed; not bound to a deployed service. **BLOCKED** |
| `worker_os_identity_separated` | Worker UID is configured; separate deployed Agent/Broker/Worker identities unverified. **BLOCKED** |
| `network_disabled` | Local Worker container tests exercise restricted networking; target Agent/deployment proof absent. **BLOCKED** |
| `docker_socket_unavailable` | No target Agent/Broker identity audit or deployment mount proof. **BLOCKED** |
| `read_only_root_filesystem` | Local Worker profile/container checks pass; target deployment attestation absent. **BLOCKED** |
| `data_mount_read_only` | Current run used no deployment data mount; intended mount policy unverified. **BLOCKED** |
| `stores_write_restricted` | No target deployment Store permissions or separate Store audit. **BLOCKED** |
| `resource_limits_enforced` | Local Worker profile is digest-bound; readiness against the target service is absent. **BLOCKED** |

The readiness verifier requires an independently trusted report matching the Worker digest and no older than five minutes. A CI container test is not a substitute. The known Agent isolation audit also records that the actual Agent process is not integrated with the Worker profile and that no physical restriction prevents the trusted process from opening mixed-year raw files before filtering.

## PR-10 — Agent real-data E2E

The seven-tool profile is implemented and its exact schema/arguments are tested: `get_data_coverage`, `create_research_request_v2`, `submit_factor_v2`, `run_factor_v2`, `get_task_status`, `get_task_report`, and `get_history_v2`. The real Catalog coverage gateway was exercised directly against the new BTC partitions, but the full Agent MCP sequence was not run.

No production Alpha Lab service or Worker containers were running during the current process check. The only observed OpenCode process was not shown to load the Alpha Lab profile or run inside the Worker boundary. Because PR-09 readiness is absent, dispatch was not attempted. No request was submitted, no Worker job ran, no output/label-separation report was emitted, and no new Evidence ID exists. Interruption/recovery, duplicate submit, and unauthorized request behavior remain covered only by synthetic/local service tests, not by a real Agent deployment replay.

Formal inference remains `BLOCKED`. Any future PR-10 Evidence must preserve that field and bind the exact Request/Campaign, Catalog snapshot and partition hashes, Worker digest, evaluator identity, and Evidence identity. Until the target-host readiness and physical data boundary are verified, PR-10 stays **`BLOCKED_NOT_RUN`**.

## Required next actions to unblock

1. Have the Alpha Lab Code Owner review the pandas 3 timestamp-unit defect and the required service-manifest update; preserve existing historical manifests and publish a new reviewed digest/version as governance requires.
2. Correct the service CI dependency setup so the locked service suite can import the checked-out Freqtrade package, then obtain a current remote workflow result on the proposed PR HEAD.
3. Configure the trusted target deployment with separate identities, read-only bounded data mounts, no network/Docker socket for the Agent, Store restrictions, and enforced resource limits. Produce a trusted readiness report for the exact Worker digest within the five-minute validity window, including shell and symlink escape negatives for the actual Agent process.
4. Only after PR-09 passes, run the seven-tool Agent MCP flow end to end on the authorized BTCUSDT perpetual Momentum request and preserve its request, data, worker, evaluator, and Evidence identities. Keep `formal_inference_status=BLOCKED`.
