# Alpha Lab local Factory on WSL

The local runtime has one WSL Broker and one MCP service container. The Broker
binds only to 127.0.0.1:8765 and is the only process that invokes Docker. The
MCP service uses host networking only to reach that loopback Broker and serve
OpenCode on 127.0.0.1:8000; it has no Docker socket, a read-only root, no Linux
capabilities, and a separate private Store mount. The Worker itself uses the
pinned profile, no network, no host mounts, an unprivileged UID, and fixed
resource ceilings.

The container mounts the existing PR09 Catalog and normalized Parquet
partitions read-only. These partitions were derived from the existing BTC
futures 5m Feather file and carry its source hash. The raw Feather file is not
mounted into the MCP service. The mounted normalized dataset has 2022–2024
partitions only. The Campaign is dataset_builder_acceptance v1; the request
registry accepts BTC futures OHLCV time-series requests in its discovery split
only. The current Catalog has a registered 5m dataset. The Request schema
retains timeframe; DatasetBuilder resolves timeframe-specific availability
rules and the Campaign currently authorizes 5m and 1h. Other periods require a
registered Catalog identity and Campaign authorization.

## Start and stop

From WSL at the repository root:

```bash
bash scripts/alpha_lab_wsl_start.sh
bash scripts/alpha_lab_wsl_stop.sh
```

The startup defaults use /tmp/alpha-lab-pr09/catalog-v3 and
/tmp/alpha-lab-pr09/normalized. Override them with ALPHA_LAB_CATALOG_ROOT and
ALPHA_LAB_NORMALIZED_ROOT when those existing read-only acceptance artifacts
are elsewhere. Startup verifies the pinned Worker image ID, actual MCP Docker
container settings, and in-container mount/socket/cgroup probes. Any failed
probe prevents readiness.

Tokens and the Evidence Store are created under
$XDG_STATE_HOME/alpha-lab-local (or ~/.local/state/alpha-lab-local) with
private file permissions. OpenCode receives only the MCP token. Do not add the
Broker token or Factory configuration to OpenCode.

## OpenCode

Merge examples/opencode.alpha-lab.jsonc into the OpenCode config. Start
OpenCode from WSL with the MCP token in its environment:

```bash
export ALPHA_LAB_MCP_TOKEN="$(<"${XDG_STATE_HOME:-$HOME/.local/state}/alpha-lab-local/secrets/mcp-token")"
opencode
```

OpenCode connects to http://127.0.0.1:8000/mcp. The MCP profile exposes only
coverage, authorized request creation, factor submit/run, task status/report,
and bounded history.

## Real end-to-end acceptance

The operator can run the seven-tool acceptance harness after startup:

```bash
export ALPHA_LAB_MCP_TOKEN="$(<"${XDG_STATE_HOME:-$HOME/.local/state}/alpha-lab-local/secrets/mcp-token")"
uv run --locked --project alpha_lab_service python -m alpha_lab_local.e2e \
  --store "${XDG_STATE_HOME:-$HOME/.local/state}/alpha-lab-local/store/experiments.sqlite3"
```

It calls all seven tools over MCP, runs a real stateless BTC 5m momentum factor
for 2024-01-01 through 2024-01-08 within the 2022–2024 Discovery interval, and
checks the returned Evidence hash against the append-only SQLite Evidence row.
The expected one-symbol result is descriptive/diagnostic; formal inference
remains BLOCKED.
