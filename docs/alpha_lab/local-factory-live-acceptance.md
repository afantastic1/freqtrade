# Alpha Lab local Factory live acceptance

Date: 2026-10-21. This run used the local WSL Broker, rebuilt MCP service image,
registered BTC futures 5m Catalog, 2022–2024 Discovery partitions, and the real
Docker Worker pinned by
`sha256:bb2aced00809732898f7c62c4a525af71beebc1190d60ba78c0924ee5862119f`.
No mock Worker or mock Evidence was used.

## MCP execution

The acceptance harness called all seven Agent tools over Streamable HTTP:
`get_data_coverage`, `create_research_request_v2`, `submit_factor_v2`,
`run_factor_v2`, `get_task_status`, `get_task_report`, and `get_history_v2`.
The submitted Python factor was a parameterized 12-bar BTC momentum calculation.
The authorized interval was 2024-01-01 through 2024-01-08 UTC, within the
Campaign's 2022–2024 Discovery source window. All 2016 requested 5m rows had
factor outputs.

Persistent Evidence Store identifiers:

- Task: `ff22d340576f589a30e5f4e74ae5c65f`
- Run/request identity: `ff22d340576f589a30e5f4e74ae5c65ffb9520b1b5056a1ae2bc8f7afdd15eb4`
- Evidence SHA-256: `cb2f340580730c1fa118a8944f0cbe2ef7464142d9f62e7feacb3f5dffd9cf62`
- Campaign policy SHA-256: `b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f`

The persisted report status is `EXPLORATORY`; formal inference remains
`BLOCKED`. Descriptive diagnostics report coverage 1.0, IC -0.0725657252,
rank IC -0.0514444075, and mean absolute exposure change 0.2057529836. These
are descriptive results for this one requested interval, not a trading claim.

## Runtime checks and regression

The WSL host preflight passed all checks for host Docker permission, loopback
Broker binding, read-only Catalog and normalized data, BTC dataset identity,
2022–2024-only Discovery source, private state, service UID and resource limits,
no Docker socket, dropped capabilities, no-new-privileges, and Worker digest.
The MCP container passed checks for read-only mounts/root, no Docker socket,
network-disabled Worker execution, resource limits, restricted Store writes,
immutable Worker digest, and isolated Worker OS identity.

The previous pinned Worker image had a v2 parameter-validation defect: its
generator iterated parameter values while referring to an undefined key. The
validator now iterates key/value pairs, and the checked-in Docker Worker image
is rebuilt and pinned by the digest above. A real-container regression test
executes a factor with a nonempty `lookback` parameter.

The MCP image is built from the locked service-only environment and starts the
Factory successfully. PyArrow is a direct service runtime dependency because
Catalog v3 reads normalized Parquet partitions. Alpha Lab data modules now use
the standalone timeframe conversion helper rather than importing
`freqtrade.exchange`; the Freqtrade history adapter remains the explicit
integration boundary.
