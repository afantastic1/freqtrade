#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ft.sh <action> [Freqtrade arguments]

Actions:
  doctor                 Check local Docker/Freqtrade project prerequisites
  version                Print the Freqtrade version
  download-data          Download historical data
  list-data              List available historical data
  backtest               Run backtesting
  backtesting-analysis   Analyze backtest trades
  hyperopt               Run hyperparameter optimization
  hyperopt-list          List hyperopt results
  lookahead-analysis     Detect lookahead bias
  recursive-analysis     Detect indicator startup variance
  dry-run                Run trade command; config must have dry_run=true
  live                   Run trade command with strict live confirmation gate
  raw                    Pass remaining arguments to Freqtrade

Environment:
  FT_COMPOSE_FILE         Compose file, default: ./docker-compose.yml
  FT_SERVICE              Compose service, default: freqtrade
  FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES   Required for live action
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${FT_COMPOSE_FILE:-./docker-compose.yml}"
SERVICE="${FT_SERVICE:-freqtrade}"

[[ $# -ge 1 ]] || {
  usage
  exit 2
}

ACTION="$1"
shift

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

run_ft() {
  compose run --rm "${SERVICE}" "$@"
}

find_config_arg() {
  local previous=""
  for arg in "$@"; do
    if [[ "${previous}" == "--config" || "${previous}" == "-c" ]]; then
      printf '%s\n' "${arg}"
      return 0
    fi
    previous="${arg}"
  done
  printf '%s\n' "user_data/config.json"
}

validate_local_config() {
  local config
  config="$(find_config_arg "$@")"
  [[ -f "${config}" ]] || die "Config not found locally: ${config}"
  python3 "${SCRIPT_DIR}/validate_config.py" "${config}"
}

require_project() {
  command -v docker >/dev/null 2>&1 || die "Docker is not installed."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable."
  [[ -f "${COMPOSE_FILE}" ]] || die "Compose file not found: ${COMPOSE_FILE}"
}

case "${ACTION}" in
  doctor)
    require_project
    printf 'Docker: '
    docker --version
    printf 'Compose: '
    docker compose version
    printf 'Compose file: %s\n' "${COMPOSE_FILE}"
    compose config --quiet
    printf 'Freqtrade: '
    run_ft --version
    ;;
  version)
    require_project
    run_ft --version
    ;;
  download-data|list-data|backtest|backtesting-analysis|hyperopt|hyperopt-list|lookahead-analysis|recursive-analysis)
    require_project
    run_ft "${ACTION}" "$@"
    ;;
  dry-run)
    require_project
    validate_local_config "$@"
    python3 "${SCRIPT_DIR}/validate_config.py" --require-dry-run "$(find_config_arg "$@")"
    run_ft trade "$@"
    ;;
  live)
    require_project
    [[ "${1:-}" == "--confirm-live" ]] ||
      die "Live mode requires --confirm-live as the first argument."
    shift
    [[ "${FREQTRADE_I_UNDERSTAND_LIVE_RISK:-}" == "YES" ]] ||
      die "Set FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES after explicit approval."
    validate_local_config "$@"
    python3 "${SCRIPT_DIR}/validate_config.py" --require-live "$(find_config_arg "$@")"
    printf 'LIVE TRADING REQUEST ACCEPTED BY LOCAL SAFETY GATE.\n' >&2
    printf 'Verify exchange, pair list, stake, max_open_trades, and stop-loss now.\n' >&2
    run_ft trade "$@"
    ;;
  raw)
    require_project
    [[ $# -ge 1 ]] || die "raw requires a Freqtrade command."
    run_ft "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    die "Unknown action: ${ACTION}"
    ;;
esac

