# Progress

- 2026-10-21: Read service/runtime/broker/isolation/catalog/registry/store/gateway and relevant tests.
- 2026-10-21: Confirmed my_dev clean at 9d52ebe9; found real BTC Feather and PR09 Catalog.
- 2026-10-21: Docker socket currently denies the sandbox user; real validation requires approved escalation.
- 2026-10-21: Added strict local runtime config and Factory composition, Discovery request/coverage guards, timeframe-derived availability rules, live isolation probes, Docker host preflight, MCP acceptance harness, WSL lifecycle scripts, and OpenCode config example.
- 2026-10-21: Added service v5, governance v1.6, and active core v1.3 manifests while retaining historical manifests. Governance check passed.
- 2026-10-21: Focused local/manifest tests passed (25); complete Alpha Lab service suite passed (188) with actual Docker Worker image `sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced` (includes the existing real Worker container tests).
- 2026-10-21: Docker access was approved. Worker digest verified locally. MCP image build could not get past the pinned GHCR tool layer pull; Docker daemon has a registry mirror but no configured proxy, while the host shell reaches GHCR through its proxy. No MCP container, seven-tool run, or persistent Evidence from the new Factory was produced.
- 2026-10-21: Added a fail-closed preflight that requires exactly the mounted 2022, 2023, and 2024 source partitions and confirms Docker daemon access. Re-ran focused tests and governance successfully.

