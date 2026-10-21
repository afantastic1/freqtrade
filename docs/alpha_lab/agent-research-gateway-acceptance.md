# Alpha Lab Agent Research Gateway Acceptance

**Status: blocked for production Agent execution.** The restricted Agent profile and trusted service boundaries pass local synthetic checks, but the real-data and deployment-isolation gates remain independently blocked.

## Evidence

- Acceptance subject code HEAD: `81805ccc60f7d2ff43f96f1cc625cbb500000000` (the Agent MCP implementation commit; this report and its acceptance tooling are subsequent acceptance records).
- Campaign policy fixture: `dataset_builder_acceptance-v1.json`, SHA-256 `ed7d76c355fd5afcd045183bacab9b86e3393f25c66c3a8c9cfa33bee03a8591a`.
- Catalog preflight: five available Catalog V3 snapshots were checked for Binance Futures BTC OHLCV 5m over `[2026-01-01, 2026-10-01)`. Each reported `MISSING`, `observed_count=0`, and no partitions. The reviewed snapshot locations are recorded in `alpha-lab-roadmap-findings.md`; they are not bundled into this repository as data evidence.
- Real request digest and Evidence identity: none; no real-data request was executed.
- Isolation proof digest: none. No independently verifiable host/container readiness evidence was available.
- Synthetic gateway, authorization, MCP registration, authentication, and compatibility test results are recorded separately in the JSON report. They do not count as real-data or host-isolation evidence.
- The execution gate remains default-deny. Agent calls cannot dispatch until a fresh trusted verifier confirms all eight checks in `isolation_v2.REQUIRED_ISOLATION_CHECKS_V2` against the configured immutable Worker image.
- `alpha_lab/freeze-manifest-v1.1.json` remains byte-identical (SHA-256 `692eda36c07f9dfd113dc57d470f7db15507fc75321bc5a79bf5edf9ff9561c2`).

## Gate results

| Gate | Result | Evidence needed to clear |
| --- | --- | --- |
| Synthetic service and MCP checks | Passed | Recorded test runs; synthetic-only |
| BTC 2026 real data | `BLOCKED_DATA_UNAVAILABLE` | Verified Catalog coverage and pinned manifest/partition hashes |
| Worker host/container isolation | `BLOCKED_ISOLATION_UNVERIFIED` | Fresh, independently reviewed readiness report for all required controls |
| Dispatch and Evidence | Not run | Both preceding gates must pass |
| Remote branch protection | Unverified | Repository integration cannot read classic branch protection; ruleset listing alone is insufficient |

The machine-readable report is [agent-research-gateway-acceptance.json](agent-research-gateway-acceptance.json). A blocked report is a truthful acceptance outcome, not a successful end-to-end release acceptance.
