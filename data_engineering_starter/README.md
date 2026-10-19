# Alpha Lab Data Engineering — Starter v0.1

A tested, maintainer-owned foundation for the existing `afantastic1/freqtrade` project.

- Maintainer-only explicit DefiLlama chain-TVL fetch, raw response preserved with digest/receipt.
- Point-in-time-unverified fundamental snapshots can **never** be exported as market panels.
- Multi-asset OHLCV market CSV validation for 5m, 15m, 1h, 4h, 1d, without filling gaps.
- Immutable, content-addressed SHA-256 snapshots and tamper validation.
- Existing Freqtrade Feather files remain unchanged; `inventory` reads metadata only.
- `export-market` creates an Alpha Lab-compatible panel CSV without overwriting it.
- No automated access to 2025+ reserve, no live trading, and no core modifications.

## Install

Unzip and run from this folder:

```bash
bash install.sh /path/to/freqtrade
cd /path/to/freqtrade
.venv/bin/python -m data_engineering --help
.venv/bin/python -m pytest -q tests/test_data_engineering.py
```

See `docs/data_engineering.md` for safe onboarding commands, data isolation rules and open gates.
