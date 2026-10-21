# Alpha Lab Agent Research Gateway

## Goal and constraints

PR-07 provides an Agent a narrow, auditable path to discover authorized data coverage, create a Campaign-authorized ResearchRequest, submit source content, inspect task status and report, and read research history. It must not grant the Agent access to host paths, raw data, Catalog mutation, Evidence/Decision mutation, arbitrary data downloads, or permission elevation.

Research execution remains unavailable by default. `run_factor_v2` may execute only when the trusted runtime supplies a verifiable isolation-readiness proof covering the Worker image, OS identity separation, read-only data access, write-restricted stores, and bounded execution. Missing or invalid proof returns `EXECUTION_BLOCKED`; an MCP bearer token alone is not isolation evidence. Synthetic tests validate the gate but do not qualify real 2026 data for Agent access.

## Trusted service boundaries

The MCP surface uses trusted dependencies injected when the server is created. The caller cannot supply dependency implementations, store roots, Catalog roots, policy contents, or execution flags.

- `get_data_coverage` accepts a bounded dataset identifier and interval. It queries only a configured read-only Catalog/source set and returns explicit coverage and source status. It accepts no filesystem paths and performs no refresh or mutation.
- `create_research_request_v2` accepts a canonical request body and Campaign identifier. The service loads the Campaign and policy from the trusted registry, validates authorization, resolves dataset identities through the trusted Catalog, canonicalizes the request, and appends it to a trusted request registry. Caller-supplied policy, digests, dataset manifests, and filesystem references are rejected or ignored rather than trusted.
- `submit_factor_v2` accepts source text and a ResearchRequest ID. It validates the request against the trusted registry, applies source size/parameter bounds, and records a task bound to the authorized Campaign, request digest, and dataset identity. It does not accept source paths, arbitrary input files, or caller-selected data scope.
- `get_task_status`, `get_task_report`, and `get_history_v2` are bounded read-only lookups. They return explicit missing/unavailable status and preserve request, task, Evidence, and store provenance.
- `run_factor_v2` checks the trusted isolation proof immediately before dispatch. If readiness is absent, it returns `EXECUTION_BLOCKED` without starting a worker or creating Evidence. With valid readiness it dispatches only a registered, bounded task under the pinned image and authorized request.

## Request and task lifecycle

The Agent first queries coverage, then creates a ResearchRequest within a trusted Campaign. Request creation is append-only and records the policy digest and canonical request digest. Source submission references that immutable request; task creation cannot widen symbols, dates, mode, or datasets. Task status progresses through explicit queued/running/terminal states, and task reports refer only to complete Evidence. Partial execution never creates Evidence.

The gateway uses stable error codes for authorization denial, invalid request, unknown identifiers, unavailable stores, and blocked execution. Responses are JSON-safe and bounded in size. History uses the PR-06 unified read-only facade and never exposes raw data or local paths.

## Data and 2026 acceptance

Coverage discovery is read-only and reports actual Catalog-backed coverage for the requested interval. The required BTC 2026-01 through 2026-09 5-minute momentum chain is a distinct acceptance gate: it requires verified interval coverage, a Campaign-authorized request, source-content submission, a complete bounded task, descriptive Evidence, and retrievable status/report/history. If the interval data or trusted isolation proof is unavailable, the acceptance record states the specific gate as blocked and does not substitute synthetic data or grant access.

## Compatibility and governance

Existing v1 MCP tools remain compatible during the migration but are not exposed through the Agent v2 profile. The fixed local legacy gateway remains separate. The Agent profile allows only the narrow MCP tools and candidate-source/config editing already authorized by policy; it cannot invoke shell, download tools, Catalog writes, store writes, or OS privilege changes. Static policy tests are documented as such and are not represented as proof of host enforcement.

## Validation

Tests cover Catalog coverage bounds and read-only behavior; Campaign authorization and registry append-only semantics; rejection of caller paths and forged provenance; task binding to the authorized request; bounded source submission; task status/report/history responses; default-deny execution when isolation proof is absent or malformed; no task dispatch and no Evidence on that path; and successful dispatch only with a trusted test proof. An integration acceptance records real BTC 2026 coverage and, only if deployment isolation is verified, the full Agent request-to-Evidence-to-history chain. Otherwise it records `EXECUTION_BLOCKED` and keeps the Agent execution gate closed.
