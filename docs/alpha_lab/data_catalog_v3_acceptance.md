# Data Catalog v3 Acceptance

Date: 2026-10-20  
Base: `58aec42937a015b33cb987ef4f7138af44d6f3a4`  
Implementation HEAD: `d7f80a2053`
Worktree: `/tmp/freqtrade-pr02-fix`

## Result

PR-02 Data Catalog v3 acceptance passed for the implementation and the local BTC futures source. The v1/v2 catalog code and schemas remain available, and the repository’s active governance verification passed. The report does not authorize merging, pushing, or opening the statistical inference gate.

## Real BTC 5m evidence

Source: `/home/von/workspace/code/coin/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-5m-futures.feather`  
Catalog output: `/tmp/alpha-catalog-v3-review-final-acceptance`
Rows: `503607`  
Observed range: `[2022-01-01T00:00:00Z, 2026-10-15T15:10:00Z]`  
Monthly partitions: `58`  
Discovery: `AVAILABLE`  
Source SHA-256: `f39767aefb7f9e2b6651912a1e84b85fef3ac6ada8a6026eeb153d93914d589c`  
Dataset ID: `binance-futures-btc-ohlcv-5m-6c7cb41c78f4`  
Manifest fingerprint: `f7478bbe08a2a6bb8da2af12f63aa84572b0e2f825ae50b86cb8ec5923614f12`  
Repeated registration returned the same fingerprint.

| Requested interval `[start, end)` | Observed | Missing | Status |
|---|---:|---:|---|
| 2025-01-01T00:00:00Z to 2026-01-01T00:00:00Z | 105120 | 0 | AVAILABLE |
| 2026-10-01T00:00:00Z to 2026-10-15T15:15:00Z | 4215 | 0 | AVAILABLE |

The second interval ends one 5-minute timeframe after the latest observed candle. The source file was read-only throughout acceptance. The first real-data run exposed a timestamp resolution mismatch; coverage and fingerprinting were corrected to normalize timestamps to nanoseconds, and the acceptance was rerun successfully.

## Synthetic and compatibility evidence

The contract and CLI fixtures cover invalid and naive timestamps, non-UTC offsets, subsecond timeframe alignment, duplicates, missing candles, OHLCV bounds and values, sparse Funding/OI behavior, source hashes, partition fingerprints, exclusive ends, and CLI routing. Coverage tests include full, leading, trailing, internal, unknown-dataset, bounded gap-list, and out-of-manifest-partition cases. Additional regressions cover source-root and catalog ancestor symlinks, malformed manifests, native Freqtrade mark candles, distinct settlement symbols, invalid quality at discovery/registration/validation, cross-month ordering, and explicit event-cadence alignment and coverage.

Independent final review identified correctness and safety gaps in the initial implementation. They were fixed before acceptance: coverage now uses the source frames and partition slices validated in the same operation; discovery and registration preserve original-order quality issues through later validation; source roots and catalog ancestor paths reject symlinks; timestamp alignment uses nanoseconds and applies only to dense data or explicitly declared event cadence; malformed manifests return structured `INVALID` results scoped to the requested dataset; native mark candles map their close to canonical mark price in memory; generated IDs include a deterministic exchange-symbol hash; and `pyarrow` is declared at runtime. Event cadence remains separate from filename sampling labels and now detects off-grid observations. Source files remain unchanged.

The legacy v1/v2 tests and standalone package checks ran with the v3 suite. The `data_engineering` wheel contains both quality contract modules and `dataset-manifest-v3.schema.json`; its metadata declares `pyarrow>=14`, and installing the wheel into `/tmp` successfully imported `data_engineering.quality.contracts` and loaded the packaged schema.

## Commands and results

Repository regression and catalog tests:

```text
env PYTHONPATH=/tmp/alpha-catalog-v3-test-deps /home/von/workspace/venv/bin/python -m pytest -o addopts='' -o cache_dir=/tmp/pytest-alpha-catalog-v3-review-final -q alpha_lab_contracts/tests/ alpha_lab/tests/ alpha_lab/calibration/ tests/test_data_engineering.py tests/test_data_engineering_packaging.py tests/test_data_engineering_catalog_v3.py tests/test_data_engineering_catalog_v3_cli.py tests/test_cross_sectional_fundamental_runner.py tests/test_fundamental_asof.py tests/test_fundamental_feature_bridge.py tests/test_fundamental_registries.py tests/test_fundamentals.py tests/test_pit_audit.py tests/test_pit_evidence.py data_engineering/scripts/test_fundamental_coverage_audit.py
518 passed, 76 warnings in 19.00s
```

The warnings are existing pytest configuration warnings and pandas FutureWarnings in Alpha Lab label/statistics code.

```text
env PYTHONPATH=/tmp/alpha-catalog-v3-test-deps /home/von/workspace/venv/bin/python -m alpha_lab_service.governance
Alpha Lab v1.3 governance, service v2, and v1.1 frozen core: valid

env PYTHONPATH=/tmp/alpha-catalog-v3-test-deps /home/von/workspace/venv/bin/python -m alpha_lab.stats.selfcheck
exit 0

env PYTHONPATH=/tmp/alpha-catalog-v3-test-deps PYTHONPYCACHEPREFIX=/tmp/alpha-catalog-v3-pycache /home/von/workspace/venv/bin/python -m compileall -q alpha_lab alpha_lab_contracts data_engineering
exit 0

git diff --check
exit 0

/home/von/workspace/venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/alpha-catalog-v3-wheel ./data_engineering
Successfully built alpha-lab-data-engineering-0.1.0; wheel metadata includes `Requires-Dist: pyarrow>=14`.
```

Wheel install smoke:

```text
/home/von/workspace/venv/bin/python -m pip install --no-deps --target /tmp/alpha-catalog-v3-installed /tmp/alpha-catalog-v3-wheel/alpha_lab_data_engineering-0.1.0-py3-none-any.whl
env PYTHONPATH=/tmp/alpha-catalog-v3-installed /home/von/workspace/venv/bin/python -c 'from importlib.resources import files; import data_engineering.quality.contracts; schema=files("data_engineering").joinpath("schemas","dataset-manifest-v3.schema.json"); print({"quality_import":True,"v3_schema_packaged":schema.is_file()})'
{'quality_import': True, 'v3_schema_packaged': True}
```
