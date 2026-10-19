#!/usr/bin/env bash
set -euo pipefail
repo="${1:?Usage: bash install.sh /path/to/freqtrade-repo}"
if [[ ! -f "$repo/data_engineering/pyproject.toml" ]]; then
  echo 'Target is not an Alpha Lab Freqtrade checkout' >&2
  exit 1
fi
echo 'This transfer archive is retired. No source files were copied.' >&2
echo "Install the canonical package with: cd '$repo' && .venv/bin/python -m pip install -e 'data_engineering[test]'" >&2
exit 2
