# Alpha Lab Agent Research Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow MCP Agent gateway for Catalog-backed coverage discovery, Campaign-authorized ResearchRequest creation, source-content task submission, safe task/report/history lookup, and execution that stays closed until trusted isolation is proven.

**Architecture:** Add an append-only trusted ResearchRequest registry and a service gateway composed only from operator-injected Campaign, Catalog, Factor Service, and isolation dependencies. Publish a separate Agent MCP profile with only authorized tools; retain existing v1/v2 server behavior for other clients. Keep execution default-deny at the Agent gateway, and require a trusted readiness verifier immediately before calling the existing v2 service.

**Tech Stack:** Python 3.11+, Pydantic v2, FastMCP, existing CampaignPolicy/ResearchRequest v2 contracts, Catalog v3 read APIs, SQLite for the new request registry, pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-alpha-lab-agent-research-gateway-design.md`

## Global Constraints

- Do not accept host paths, Catalog roots, source files, policy records, manifest contents, worker identities, or execution flags from Agent input.
- Request creation and registry updates are trusted, append-only service operations; reads never mutate Catalog or existing research stores.
- Source must be submitted as bounded UTF-8 content and bound to one immutable authorized ResearchRequest.
- `run_factor_v2` through the Agent profile returns `EXECUTION_BLOCKED` without a valid trusted isolation-readiness result; an MCP bearer token or synthetic test is not isolation proof.
- Incomplete tasks never create Evidence; only complete Evidence reports may be returned.
- Preserve existing v1 MCP tools, service factories, Campaign schemas, and historical stores.
- The BTC 2026-01 through 2026-09 5m real-data acceptance is separate from synthetic tests; absent verified data or deployment isolation, record the precise blocked gate and keep Agent execution closed.

## Review Focus

- Forged caller-supplied Campaign policy, policy digest, dataset identity, or paths must never widen authority.
- Duplicate request IDs with identical payloads must be idempotent; conflicting reuse must be rejected.
- A task lookup must not expose another task's source or arbitrary local paths.
- Missing, malformed, stale, or incomplete isolation evidence must block before worker dispatch and Evidence creation.
- MCP registration must not expose legacy submit/run/decision-write tools in the Agent profile.

---

### Task 1: Append-only ResearchRequest registry

**Files:**
- Create: `alpha_lab_service/request_registry_v2.py`
- Test: `alpha_lab_service/tests/test_request_registry_v2.py`
- Reuse: `alpha_lab_contracts/campaign_policy_v1.py`, `alpha_lab_contracts/research_request_v2.py`

**Interfaces:**
- Produces `ResearchRequestRegistryV2(path, policy_registry)` with `create(payload: Mapping[str, object]) -> dict` and `get(request_id: str) -> dict`.
- `create` validates against the trusted Campaign policy registry, stores canonical ResearchRequest bytes plus request digest, and returns the canonical record.
- Identical replay returns the same record; same request ID with different canonical content raises a stable conflict error. Existing records are never updated or deleted.
- `get` implements the existing `ResearchRequestRegistryV1.get` contract used by `resolve_authorized_run`.

- [ ] **Step 1: Write failing tests** for valid authorized create/get, policy/split/symbol denial, forged unknown fields and host paths, idempotent replay, conflicting ID reuse, and read-only get.
- [ ] **Step 2: Run focused tests and confirm the missing registry/API failures.**
- [ ] **Step 3: Implement a new dedicated SQLite registry** with a single append-only request table, canonical JSON/digest columns, parameterized SQL, bounded IDs, and transaction-safe insert-or-compare behavior. Do not reuse or migrate Factor Service Store databases.
- [ ] **Step 4: Run `pytest alpha_lab_service/tests/test_request_registry_v2.py -q` and confirm all cases pass.**
- [ ] **Step 5: Commit** as `feat(alpha-lab): add trusted request registry v2`.

### Task 2: Trusted read-only Catalog coverage adapter

**Files:**
- Create: `alpha_lab_service/catalog_gateway_v2.py`
- Test: `alpha_lab_service/tests/test_catalog_gateway_v2.py`
- Reuse: `data_engineering/catalog_v3.py`, `data_engineering/data_kind_registry.py`

**Interfaces:**
- Produces `CatalogCoverageGatewayV2(catalog_root, source_roots, dataset_registry)` and `get_data_coverage(*, dataset_key: str, start: str, end: str) -> dict`.
- `dataset_registry` is operator-injected and maps bounded public keys `(exchange, market_type, asset, data_kind, timeframe)` to trusted Catalog dataset IDs. Agent input never supplies roots or source mappings.
- The adapter returns Catalog v3 status, actual interval/partition data, and stable `NOT_CONFIGURED`/`UNAVAILABLE` errors; it does not register, validate-and-repair, refresh, or download.

- [ ] **Step 1: Write failing tests** for known/unknown dataset keys, invalid/reversed intervals, missing coverage, valid partial coverage, unavailable source, path injection rejection, and Catalog/source-root byte hashes unchanged after queries.
- [ ] **Step 2: Run focused tests and confirm the new gateway is absent.**
- [ ] **Step 3: Implement the fixed-root adapter** using Catalog v3 `get_coverage` and strict UTC interval parsing; cap response size and reported gap count.
- [ ] **Step 4: Run `pytest alpha_lab_service/tests/test_catalog_gateway_v2.py -q`.**
- [ ] **Step 5: Commit** as `feat(alpha-lab): add read-only catalog coverage gateway`.

### Task 3: Agent research gateway and execution gate

**Files:**
- Create: `alpha_lab_service/agent_gateway_v2.py`
- Create: `alpha_lab_service/isolation_v2.py`
- Modify: `alpha_lab_service/runtime.py`
- Test: `alpha_lab_service/tests/test_agent_gateway_v2.py`
- Reuse: `alpha_lab_service/jobs_v2.py`, `alpha_lab_service/protocol_v2.py`, `alpha_lab_service/history_v2.py`

**Interfaces:**
- Produces `AgentResearchGatewayV2(request_registry, coverage_gateway, factor_service, isolation_verifier, input_schema_registry)`.
- Methods: `create_research_request_v2(payload)`, `submit_factor_v2(*, research_request_id, source, params, execution_mode, state_schema_id=None, state_schema_version=None)`, `run_factor_v2(task_id)`, `get_task_status(task_id)`, `get_task_report(task_id)`, and `get_history_v2(limit=100)`.
- Submission injects trusted Worker input schema identity from the authorized request mode/data-type mapping; callers cannot pick data schemas, worker identity, or evaluator identity.
- The trusted isolation verifier returns a structured readiness result pinned to the configured immutable Worker image and all required host/container controls. Missing verifier or any failed/stale check is false.
- Run checks the gate immediately before calling `ResearchServiceV2.run_factor_v2`; blocked response includes stable `EXECUTION_BLOCKED` and does not dispatch or create Evidence.
- Task/report responses are bounded, JSON-safe, sanitized, and return `NOT_FOUND` or explicit unavailable status without exposing source paths.

- [ ] **Step 1: Write failing tests** for request-to-task binding, caller scope widening rejection, source byte limit, schema injection rejection, task/report/history sanitization, complete-Evidence-only report, absent/malformed/stale isolation proof, no broker call/no Evidence on block, and valid trusted proof dispatch.
- [ ] **Step 2: Run focused tests and confirm expected failures.**
- [ ] **Step 3: Implement the gateway and default-deny verifier.** Configure runtime to construct the Agent gateway only from operator-owned dependencies; do not change legacy service execution behavior.
- [ ] **Step 4: Run `pytest alpha_lab_service/tests/test_agent_gateway_v2.py -q`.**
- [ ] **Step 5: Commit** as `feat(alpha-lab): authorize agent factor tasks`.

### Task 4: Separate Agent MCP profile

**Files:**
- Modify: `alpha_lab_service/server.py`
- Test: `alpha_lab_service/tests/test_mcp_v2_tools.py`
- Create: `alpha_lab_service/tests/test_agent_mcp_profile_v2.py`

**Interfaces:**
- Produces `create_agent_mcp_server(gateway, *, bearer_token, host, port)` exposing exactly `get_data_coverage`, `create_research_request_v2`, `submit_factor_v2`, `run_factor_v2`, `get_task_status`, `get_task_report`, and `get_history_v2`.
- Agent profile registers no v1 tools, `append_decision`, raw Catalog mutation, or arbitrary v2 dependency injection. Existing `create_mcp_server` remains backward compatible.

- [ ] **Step 1: Write failing tool registration/schema tests** that assert exactly the allowed seven names, strict tool arguments without path/root/policy/proof fields, bearer middleware, and unchanged legacy server registrations.
- [ ] **Step 2: Run the focused MCP tests and confirm profile creation fails.**
- [ ] **Step 3: Add the separate MCP constructor** and stable JSON-safe error mapping for authorization, validation, missing identifiers, unavailable stores, and blocked execution.
- [ ] **Step 4: Run `pytest alpha_lab_service/tests/test_agent_mcp_profile_v2.py alpha_lab_service/tests/test_mcp_v2_tools.py -q`.**
- [ ] **Step 5: Commit** as `feat(alpha-lab): expose restricted agent mcp profile`.

### Task 5: Acceptance, governance, and end-to-end evidence

**Files:**
- Create: `alpha_lab_service/acceptance_agent_v2.py`
- Create: `alpha_lab_service/tests/test_acceptance_agent_v2.py`
- Create: `docs/alpha_lab/agent-research-gateway-acceptance.md`
- Create: `docs/alpha_lab/agent-research-gateway-acceptance.json`
- Modify: `alpha_lab_service/protocol-manifest-v3.json`
- Modify: `.github/workflows/alpha-lab-python-service.yml`
- Modify: `alpha_lab_service/governance.py` and CI protection tests as needed

**Interfaces:**
- Acceptance report records exact code HEAD, request/Campaign digests, Catalog manifest and partition hashes, test commands/results, dispatch status, Evidence identity, and each independent data/security/governance gate.
- Real BTC 2026-01 through 2026-10 half-open 5m coverage must be verified before claiming the full chain. If Catalog data remains `MISSING`, record `BLOCKED_DATA_UNAVAILABLE`; if OS/container isolation proof is absent, record `BLOCKED_ISOLATION_UNVERIFIED`; do not synthesize a successful real-data result.
- PR-07 Agent execution remains disabled in production configuration until isolation proof is supplied and independently reviewed.

- [ ] **Step 1: Write acceptance tests** for no network/no external data side effects, distinct synthetic-vs-real labels, blocked status for missing real data, blocked status for absent isolation, and complete report provenance.
- [ ] **Step 2: Run focused acceptance tests and confirm failures.**
- [ ] **Step 3: Add bounded acceptance runner/report, governance manifest entries and CI path coverage** for all new gateway/registry/catalog/security files; leave `freeze-manifest-v1.1.json` byte-identical.
- [ ] **Step 4: Run required focused service/contracts/governance checks and `python -m alpha_lab_service.governance`; record exact outputs.**
- [ ] **Step 5: Run BTC 2026 real-data preflight.** Only run the complete request-to-Evidence acceptance if actual Catalog coverage and trusted isolation both pass; otherwise produce the explicit blocked acceptance artifact.
- [ ] **Step 6: Commit** as `test(alpha-lab): record agent gateway acceptance`.

## Plan self-review

- Spec coverage: the tasks implement trusted request creation, coverage query, source submission, status/report/history, default-deny isolated execution, separate MCP profile, and the real 2026 acceptance gate.
- Compatibility: v1 MCP and internal v2 Factor Service stay separate and backward compatible.
- Review focus cases are pinned in Tasks 1, 2, 3, and 4 tests.
- Current evidence says all five available Catalog snapshots have no BTC 2026-01 to 2026-09 observations. The plan does not treat this as a code failure or invent a substitute; Task 5 records the data gate as blocked unless an authorized source snapshot becomes available.
- Current deployment has no verifiable Agent host isolation proof. Execution stays blocked unless a trusted, independently reviewable readiness verifier is configured.
