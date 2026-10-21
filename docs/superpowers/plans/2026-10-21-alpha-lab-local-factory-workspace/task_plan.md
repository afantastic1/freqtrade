# Alpha Lab local Factory implementation

## Goal
Ship a minimal WSL Broker + isolated MCP/Factory container and prove one real BTC 5m Discovery factor run through all seven Agent MCP tools into the Evidence Store.

## Phases
- [x] 1. Add RED tests for Factory assembly and fail-closed isolation probes
- [x] 2. Implement trusted config parsing, Factory, runtime verifier and pinned MCP image
- [x] 3. Add WSL start/stop, private config template and OpenCode config example
- [x] 4. Run focused and service tests; repair regressions
- [ ] 5. Launch both containers, verify actual Docker boundaries, run MCP workflow and inspect Evidence
- [ ] 6. Final review and report remaining blockers

## Constraints
- Preserve frozen service/Alpha Lab core modules and existing data/evidence.
- Use only BTC futures OHLCV and campaign Discovery (2022-01-01 through 2025-01-01).
- No 2025+ or 2026 data reads. The test run gets a new private Store.
- Readiness must come from checks against actual container state and pinned Worker profile.
- OpenCode config receives only MCP URL and MCP token.

## Errors encountered
| Error | Attempt | Resolution |
|---|---:|---|
| Docker API permission denied in sandbox | 1 | Finish code/tests, then request approved escalated Docker access for real verification |
| Docker daemon stalls while pulling the pinned `ghcr.io/astral-sh/uv:0.11.19` build layer | 1 | Confirmed host can reach GHCR only through the approved host proxy; Docker daemon reports a Registry Mirror and has no configured proxy. Stopped the stalled pull without changing machine-wide Docker configuration. MCP container build and seven-tool Evidence acceptance remain blocked. |

