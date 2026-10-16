#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

python3 -m py_compile \
  "${SCRIPT_DIR}/validate_config.py" \
  "${SCRIPT_DIR}/analyze_backtest.py" \
  "${SKILL_DIR}/assets/SafeEmaRsiStrategy.py"

python3 "${SCRIPT_DIR}/validate_config.py" --require-dry-run \
  "${SKILL_DIR}/assets/config.dry-run.example.json"

cat >"${TMP_DIR}/result.json" <<'JSON'
{
  "strategy": {
    "SafeEmaRsiStrategy": {
      "profit_total": 0.12,
      "profit_total_abs": 120.5,
      "max_drawdown_account": 0.08,
      "sharpe": 1.2,
      "sortino": 1.5,
      "profit_factor": 1.3,
      "trades": [
        {"profit_ratio": 0.02},
        {"profit_ratio": -0.01},
        {"profit_ratio": 0.03}
      ]
    }
  }
}
JSON

python3 "${SCRIPT_DIR}/analyze_backtest.py" \
  "${TMP_DIR}/result.json" -o "${TMP_DIR}/report.md"
grep -q "SafeEmaRsiStrategy" "${TMP_DIR}/report.md"
grep -q "66.67%" "${TMP_DIR}/report.md"

if python3 "${SCRIPT_DIR}/validate_config.py" --require-live \
  "${SKILL_DIR}/assets/config.dry-run.example.json" >/dev/null 2>&1; then
  printf 'ERROR: dry-run example unexpectedly passed the live gate.\n' >&2
  exit 1
fi

printf 'All local smoke tests passed.\n'

