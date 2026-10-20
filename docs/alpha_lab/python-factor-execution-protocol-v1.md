# Python Factor Execution Protocol v1.0

## Trust boundaries

The Agent submits factor source **content** and a bounded request through MCP
Streamable HTTP. The MCP process validates and persists the declaration; it does
not import, compile, or execute the submitted source. A separate Broker accepts
only a task identifier and protocol frames, selects the fixed Worker profile,
and starts a disposable container. The Worker contains the submitted Python
execution. The trusted evaluator computes labels and descriptive metrics after
the Worker returns values.

`server.main` requires an operator-configured trusted `module:factory` and a
bearer token. The default listener is loopback. The Broker is a separate process
and defaults to loopback HTTP with its own bearer token. The MCP service must run
under an OS account that cannot access the Docker runtime socket; the Broker must
run under a separately controlled account. This repository provides process
boundaries, but does not provision those host accounts or prove their permissions.

## Request contract

`submit_factor` accepts UTF-8 source text (maximum 256 KiB), a hypothesis, a
unique declared symbol list (maximum 100), UTC start/end, timeframe, scalar JSON
parameters, and a lookback limit. Unknown fields fail validation. Requests have
no path, image, command, mount, database, or host argument. The MCP service only
receives source content; it never opens an Agent-supplied path.

The Worker source defines:

```python
def compute_factor(history, *, as_of, params):
    ...
```

`history` is a sorted unique `(open_time, symbol)` UTC DataFrame. It has only
`available_time` and allowlisted OHLCV features. Both `open_time` and
`available_time` must be no later than `as_of`. Each time section must contain
exactly the declared symbols. The request scheduler validates the full frame
before it reaches the Broker; the Broker and Worker independently reject
unknown columns, missing assets, duplicate rows, and future timestamps. Labels,
future returns, Evaluator objects, Lab source, stores and host paths are absent.
Missing feature values may be represented as JSON `null`; non-finite factor
outputs are rejected.

Each invocation returns one finite numeric value per declared symbol for that
`as_of` section. Diagnostics are scalar and bounded. Worker stdout is a framed
protocol channel with a 4 MiB output cap; stderr is not returned to the caller.
At most 512 as-of steps and 16 MiB of input are accepted per task. The configured
lookback is also checked against every step.

## Worker profile

The image uses Python 3.11.10 from a pinned base digest and a hash-locked set of
NumPy, Pandas, SciPy and PyWavelets dependencies. The runtime image has
`PIP_NO_INDEX=1`; no Worker package installation is available. Docker starts it
with network disabled, UID/GID 65532, all capabilities dropped, no-new-privileges,
Docker's built-in seccomp allowlist, read-only root, a 16 MiB no-exec temporary
filesystem, a 512 MiB memory limit, one CPU, 64 process limit, and a wall-clock
timeout. The container has no host mounts or Docker socket. Each task gets a
fresh container and temporary filesystem.

The Broker enforces a bounded concurrent container count, task timeout, input
and output byte limits, and attempts to kill the container after timeout. The
actual-container tests exercise network denial, filesystem visibility/write
denial, UID/capability/seccomp/AppArmor state, memory and PID limits, output
limits, timeout cleanup and cross-task temporary-data isolation. A passing test
only describes the tested Docker daemon and profile.

## Identity and result status

The canonical run identity binds source SHA-256, request/configuration, data
fingerprint and availability semantics, Worker image digest (including locked
dependencies), protocol, Alpha Lab engine version, statistical registry and
method statuses, and the descriptive evaluator identity. Evidence and Decision
rows are append-only and separate from both prior Research Stores.

The service exposes point IC/RankIC, coverage, descriptive stability and a
descriptive incremental comparison. Inference remains `BLOCKED`; the adapter
does not publish p-values or `SUPPORTED` conclusions. Results and Decisions may
only be tagged `EXPLORATORY` or `DIAGNOSTIC`.
