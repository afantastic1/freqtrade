#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${1:-./ft_userdata}"
COMPOSE_URL="${FREQTRADE_COMPOSE_URL:-https://raw.githubusercontent.com/freqtrade/freqtrade/stable/docker-compose.yml}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || die "Docker is required."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
command -v curl >/dev/null 2>&1 || die "curl is required to download the official compose file."

mkdir -p "${TARGET_DIR}"
TARGET_DIR="$(cd -- "${TARGET_DIR}" && pwd)"

if [[ -e "${TARGET_DIR}/docker-compose.yml" ]]; then
  die "${TARGET_DIR}/docker-compose.yml already exists; refusing to overwrite it."
fi

printf 'Downloading official Freqtrade stable Docker Compose file...\n'
curl --fail --location --proto '=https' --tlsv1.2 \
  "${COMPOSE_URL}" --output "${TARGET_DIR}/docker-compose.yml"

(
  cd "${TARGET_DIR}"
  docker compose pull
  docker compose run --rm freqtrade create-userdir --userdir user_data
)

install -m 0644 \
  "${SKILL_DIR}/assets/SafeEmaRsiStrategy.py" \
  "${TARGET_DIR}/user_data/strategies/SafeEmaRsiStrategy.py"

if [[ ! -e "${TARGET_DIR}/user_data/config.json" ]]; then
  install -m 0600 \
    "${SKILL_DIR}/assets/config.dry-run.example.json" \
    "${TARGET_DIR}/user_data/config.json"
fi

cat <<EOF

Initialized: ${TARGET_DIR}

Safety state:
  - dry_run is enabled
  - no exchange credentials were added
  - the bot was NOT started

Next:
  cd "${TARGET_DIR}"
  python3 "${SKILL_DIR}/scripts/validate_config.py" user_data/config.json
  "${SKILL_DIR}/scripts/ft.sh" doctor
EOF

