#!/usr/bin/env bash
set -euo pipefail

STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
RUN_DIR="$STATE_HOME/alpha-lab-local"
MCP_CONTAINER="alpha-lab-mcp"
BROKER_PID_FILE="$RUN_DIR/broker.pid"

if docker inspect "$MCP_CONTAINER" >/dev/null 2>&1; then
    label="$(docker inspect --format '{{index .Config.Labels "dev.freqtrade.alpha-lab-local"}}' "$MCP_CONTAINER")"
    if [[ "$label" == "true" ]]; then
        docker stop --time 30 "$MCP_CONTAINER" >/dev/null
    else
        echo "Refusing to stop an unowned container named $MCP_CONTAINER." >&2
        exit 2
    fi
fi

if [[ -f "$BROKER_PID_FILE" ]]; then
    broker_pid="$(<"$BROKER_PID_FILE")"
    if [[ "$broker_pid" =~ ^[0-9]+$ ]] && kill -0 "$broker_pid" 2>/dev/null; then
        kill "$broker_pid"
        for _ in $(seq 1 40); do
            kill -0 "$broker_pid" 2>/dev/null || break
            sleep 0.25
        done
    fi
    rm -f "$BROKER_PID_FILE"
fi

echo "Alpha Lab MCP and WSL Broker stopped. Private Store and tokens were preserved."
