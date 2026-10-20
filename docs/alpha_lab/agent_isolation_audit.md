# Alpha Lab Agent isolation audit

**Disposition: BLOCKED for Agent admission.** A Docker worker probe passed, but the actual Agent runtime is not forced into that worker and OpenCode did not load the configured agent in the tested container context. Prompt restrictions are not treated as a security boundary.

## Required permission matrix

| Resource | Research Agent access contract | Current proof |
|---|---|---|
| Frozen research core and CI/manifest | Read-only only where needed to understand protocol; no edit, delete, chmod, or replacement | Docker worker probe mounted `alpha_lab/` read-only; attempted `runner.py` write failed. Actual Agent runtime integration remains unverified. |
| Raw market sources and 2025+ holdout | No mount/read; data only via bounded panel/gateway | Docker probe confirmed raw path absent. Panel itself is bounded and separately hashed. Actual Agent mount policy is not active. |
| Bounded 2022–2024 Panel | Read-only to trusted fixed engine; Agent uses panel ID/gateway, no direct filesystem reads | Panel currently resides in repository `user_data`; `0444` is a discretionary mode, not protection from same-UID code. Actual runtime mount policy remains unverified. |
| Evidence and Decision stores | No direct file/SQLite read or write. History via sanitized DTO; Decisions appended only through trusted gateway to a separate versioned store | Docker probe confirmed store/evidence/decision paths absent. Current legacy DB lacks v1.1 CS tables, so storage gate blocks. |
| Agent workspace | Write only request/config in isolated scratch workspace; no core, panel, evidence, or DB writes | Probe confirmed `/agent` write succeeds. OpenCode config was not loaded in the reduced container; actual allowlist remains unverified. |
| Network and subprocess | No arbitrary shell, subprocess, or network; only exact controlled gateway executable when admitted | `--network none` probe denied external TCP. bwrap `--unshare-net` failed with `Operation not permitted`. The exact Alpha Lab gateway was not run. |

## Executed OS-level probe

An ephemeral Docker worker was launched with network `none`, read-only root, UID/GID `1000:1000`, dropped capabilities, `no-new-privileges`, a 32-process limit, read-only `alpha_lab/` mount, no market/store mounts, and one `/tmp`-backed writable Agent directory. Negative checks returned:

```text
CORE_WRITE_DENIED
PROTECTED_PATH_ABSENT:/protected/market
PROTECTED_PATH_ABSENT:/protected/evidence.sqlite
PROTECTED_PATH_ABSENT:/protected/decisions
AGENT_WORKSPACE_WRITE_ALLOWED
NETWORK_DENIED
```

This proves those boundaries for a process actually launched with this container profile. It does **not** prove that OpenCode or Codex delegates every Agent tool call to this profile.

A second read-only container probe mounted only the bounded Panel at `/research/panel.csv` and the core read-only. It scanned all rows without printing values, checked the fixed row/asset counts and the open/availability cutoff, and returned `BOUNDED_PANEL_ONLY_READABLE`. Raw Futures, historical Evidence, and Decision stores were not mounted. This establishes that the resulting panel can be exposed without exposing those source/store paths; it still does not establish actual Agent runtime enforcement.

## Actual runtime checks

- Host OpenCode could not write its normal log because `/home/von/.local/share/opencode/log` is read-only.
- The installed OpenCode ELF binary was launched in the Docker worker with project/config mounted read-only, raw Futures path masked, Research Store path replaced by scratch, and network disabled. `opencode debug agents` returned `[]`; the configured `alpha-researcher` was not loaded in this context.
- The Codex host Agent is provided by the managed execution environment and cannot be reconfigured or introspected from this repository. No evidence shows it runs within the Docker profile.
- A host `bwrap --unshare-user --unshare-pid --unshare-net ...` probe failed before process launch: `Failed to create NETLINK_ROUTE socket: Operation not permitted`.
- The container profile itself does not deny shell spawning or perform an adversarial symlink escape test. Those checks are false/unverified in the attestation and block admission; a runtime policy parser returning an empty Agent list cannot prove them.

## Admission consequence and minimum unblock

The machine attestation at [agent_isolation_attestation.json](agent_isolation_attestation.json) records successful worker-level negative checks and `actual_agent_runtime_enforced=false`. A maintainer must provide a supported launcher/runtime integration that runs the **actual** Agent process and all tool subprocesses inside the restricted worker, then rerun adversarial attempts to write the core/store, read raw/held-out data, follow symlinks outside the mount, spawn a shell, and connect to network. An actual configured benign Agent run must also show the fixed gateway works while all forbidden operations fail. Until then, Agent isolation is `BLOCKED`.
