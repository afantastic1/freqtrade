#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
RUN_DIR="$STATE_HOME/alpha-lab-local"
STATE_DIR="$RUN_DIR/store"
SECRET_DIR="$RUN_DIR/secrets"
CATALOG_ROOT="${ALPHA_LAB_CATALOG_ROOT:-/tmp/alpha-lab-pr09/catalog-v3}"
NORMALIZED_ROOT="${ALPHA_LAB_NORMALIZED_ROOT:-/tmp/alpha-lab-pr09/normalized}"
FREQTRADE_DATADIR="${ALPHA_LAB_FREQTRADE_DATADIR:-$ROOT/user_data/data/binance}"
WORKER_IMAGE="sha256:bb2aced00809732898f7c62c4a525af71beebc1190d60ba78c0924ee5862119f"
SERVICE_IMAGE="alpha-lab-local-service:my-dev"
MCP_CONTAINER="alpha-lab-mcp"
BROKER_PID_FILE="$RUN_DIR/broker.pid"
BROKER_LOG="$RUN_DIR/broker.log"
STARTED_BROKER_PID=""

cleanup_on_failure() {
    result=$?
    if [[ "$result" -ne 0 ]]; then
        if docker inspect "$MCP_CONTAINER" >/dev/null 2>&1; then
            label="$(docker inspect --format '{{index .Config.Labels "dev.freqtrade.alpha-lab-local"}}' "$MCP_CONTAINER" 2>/dev/null || true)"
            if [[ "$label" == "true" ]]; then
                docker stop --time 5 "$MCP_CONTAINER" >/dev/null 2>&1 || true
            fi
        fi
        if [[ -n "$STARTED_BROKER_PID" ]] && kill -0 "$STARTED_BROKER_PID" 2>/dev/null; then
            kill "$STARTED_BROKER_PID" 2>/dev/null || true
        fi
        rm -f "$BROKER_PID_FILE"
    fi
}

for required in "$CATALOG_ROOT/catalog-v3/datasets" "$NORMALIZED_ROOT"; do
    if [[ ! -d "$required" ]]; then
        echo "Required Alpha Lab Catalog/source directory is missing." >&2
        echo "Set ALPHA_LAB_CATALOG_ROOT and ALPHA_LAB_NORMALIZED_ROOT to existing read-only data roots." >&2
        exit 2
    fi
done

if ! compgen -G "$CATALOG_ROOT/catalog-v3/datasets/*/*.json" >/dev/null; then
    echo "The Alpha Lab Catalog has no registered dataset manifests." >&2
    exit 2
fi

if [[ ! -d "$FREQTRADE_DATADIR" || ! -d "$FREQTRADE_DATADIR/futures" ]]; then
    echo "Freqtrade local Spot/Futures data directory is missing: $FREQTRADE_DATADIR" >&2
    echo "Set ALPHA_LAB_FREQTRADE_DATADIR to the existing local datadir; no download is performed." >&2
    exit 2
fi

mkdir -p "$RUN_DIR" "$STATE_DIR" "$SECRET_DIR"
chmod 700 "$RUN_DIR" "$STATE_DIR" "$SECRET_DIR"
if [[ ! -f "$SECRET_DIR/broker-token" ]]; then
    umask 077
    openssl rand -hex 32 > "$SECRET_DIR/broker-token"
fi
if [[ ! -f "$SECRET_DIR/mcp-token" ]]; then
    umask 077
    openssl rand -hex 32 > "$SECRET_DIR/mcp-token"
fi
chmod 600 "$SECRET_DIR/broker-token" "$SECRET_DIR/mcp-token"
BROKER_TOKEN="$(<"$SECRET_DIR/broker-token")"
MCP_TOKEN="$(<"$SECRET_DIR/mcp-token")"
if [[ ${#BROKER_TOKEN} -lt 24 || ${#MCP_TOKEN} -lt 24 ]]; then
    echo "Private Alpha Lab tokens must contain at least 24 characters." >&2
    exit 2
fi

docker image inspect "$WORKER_IMAGE" >/dev/null
docker build -f "$ROOT/containers/alpha-lab-local-service/Dockerfile" -t "$SERVICE_IMAGE" "$ROOT"

if docker inspect "$MCP_CONTAINER" >/dev/null 2>&1; then
    label="$(docker inspect --format '{{index .Config.Labels "dev.freqtrade.alpha-lab-local"}}' "$MCP_CONTAINER")"
    if [[ "$label" != "true" ]]; then
        echo "Refusing to replace an unowned container named $MCP_CONTAINER." >&2
        exit 2
    fi
    state="$(docker inspect --format '{{.State.Status}}' "$MCP_CONTAINER")"
    if [[ "$state" == "running" ]]; then
        docker stop --time 30 "$MCP_CONTAINER" >/dev/null
    fi
    docker rm "$MCP_CONTAINER" >/dev/null
fi

if [[ -f "$BROKER_PID_FILE" ]] && kill -0 "$(<"$BROKER_PID_FILE")" 2>/dev/null; then
    echo "A broker process is already recorded at $BROKER_PID_FILE." >&2
    exit 2
fi
rm -f "$BROKER_PID_FILE"

(
    cd "$ROOT"
    export PYTHONPATH="$ROOT"
    export ALPHA_LAB_BROKER_TOKEN="$BROKER_TOKEN"
    exec uv run --locked --project "$ROOT/alpha_lab_service" \
        python -m alpha_lab_service.broker_server \
        --profile "$ROOT/containers/alpha-lab-factor-worker/profile.json" \
        --host 127.0.0.1 --port 8765
) >"$BROKER_LOG" 2>&1 &
STARTED_BROKER_PID="$!"
echo "$STARTED_BROKER_PID" > "$BROKER_PID_FILE"
trap cleanup_on_failure EXIT

broker_ready=0
for _ in $(seq 1 240); do
    if (echo > /dev/tcp/127.0.0.1/8765) >/dev/null 2>&1; then
        broker_ready=1
        break
    fi
    sleep 0.25
done
if [[ "$broker_ready" != 1 ]]; then
    echo "WSL Broker failed to start; broker log follows:" >&2
    tail -80 "$BROKER_LOG" >&2 || true
    kill "$(<"$BROKER_PID_FILE")" 2>/dev/null || true
    exit 2
fi

MCP_ENV="$RUN_DIR/mcp.env"
umask 077
cat > "$MCP_ENV" <<EOF
ALPHA_LAB_MCP_TOKEN=$MCP_TOKEN
ALPHA_LAB_BROKER_TOKEN=$BROKER_TOKEN
ALPHA_LAB_SERVICE_FACTORY=alpha_lab_local.factory:create
ALPHA_LAB_MCP_PROFILE=agent
ALPHA_LAB_LOCAL_CONFIG=/etc/alpha-lab/runtime.json
ALPHA_LAB_MCP_HOST=127.0.0.1
ALPHA_LAB_MCP_PORT=8000
EOF
chmod 600 "$MCP_ENV"

docker run --detach --rm --name "$MCP_CONTAINER" \
    --label dev.freqtrade.alpha-lab-local=true \
    --network host --user "$(id -u):$(id -g)" \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges:true \
    --pids-limit 64 --memory 1g --cpus 1 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
    --mount "type=bind,source=$CATALOG_ROOT,target=/data/catalog,readonly" \
    --mount "type=bind,source=$NORMALIZED_ROOT,target=/data/normalized,readonly" \
    --mount "type=bind,source=$FREQTRADE_DATADIR,target=/data/freqtrade,readonly" \
    --mount "type=bind,source=$STATE_DIR,target=/var/lib/alpha-lab" \
    --env-file "$MCP_ENV" "$SERVICE_IMAGE" >/dev/null

export PYTHONPATH="$ROOT"
export ALPHA_LAB_MCP_CONTAINER="$MCP_CONTAINER"
export ALPHA_LAB_CATALOG_HOST="$CATALOG_ROOT"
export ALPHA_LAB_NORMALIZED_HOST="$NORMALIZED_ROOT"
export ALPHA_LAB_FREQTRADE_HOST="$FREQTRADE_DATADIR"
export ALPHA_LAB_STATE_HOST="$STATE_DIR"
export ALPHA_LAB_WORKER_IMAGE="$WORKER_IMAGE"
uv run --locked --project "$ROOT/alpha_lab_service" python -m alpha_lab_local.host_checks

container_ready=0
for _ in $(seq 1 80); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/mcp || true)"
    if [[ "$status" == "401" ]]; then
        container_ready=1
        break
    fi
    sleep 0.25
done
if [[ "$container_ready" != 1 ]]; then
    echo "MCP readiness check failed; container logs follow:" >&2
    docker logs "$MCP_CONTAINER" >&2 || true
    echo "MCP service failed to become ready; inspect docker logs $MCP_CONTAINER." >&2
    exit 2
fi

docker exec "$MCP_CONTAINER" python -m alpha_lab_local.isolation_check
echo "Alpha Lab MCP ready at http://127.0.0.1:8000/mcp."
echo "OpenCode token file: $SECRET_DIR/mcp-token"
echo "Broker credentials remain in the WSL launcher/runtime only."
trap - EXIT
