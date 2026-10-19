#!/usr/bin/env bash
set -euo pipefail
repo="${1:?Usage: bash install.sh /path/to/freqtrade-repo}"
if [[ ! -f "$repo/pyproject.toml" || ! -d "$repo/alpha_lab" ]]; then
  echo 'Target is not an Alpha Lab Freqtrade checkout' >&2
  exit 1
fi
for entry in data_engineering/__init__.py data_engineering/catalog.py data_engineering/defillama.py data_engineering/__main__.py docs/data_engineering.md tests/test_data_engineering.py; do
  if [[ -e "$repo/$entry" ]]; then
    echo "Refusing to overwrite $repo/$entry" >&2
    exit 1
  fi
done
for entry in data_engineering/__init__.py data_engineering/catalog.py data_engineering/defillama.py data_engineering/__main__.py docs/data_engineering.md tests/test_data_engineering.py; do
  mkdir -p "$(dirname "$repo/$entry")"
  cp "$(dirname "${BASH_SOURCE[0]}")/$entry" "$repo/$entry"
done
echo "Installed Data Engineering starter into: $repo"
echo "Next: cd '$repo' && .venv/bin/python -m pytest -q tests/test_data_engineering.py"
