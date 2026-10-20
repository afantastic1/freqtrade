# Data Catalog v3 Acceptance

Date: 2026-10-20  
Base: `58aec42937a015b33cb987ef4f7138af44d6f3a4`  
Implementation HEAD: `a97654ee735ed1f653544737549f47d24c2f9f69`  
Worktree: `/home/von/.codex/worktrees/a03f/freqtrade`

## Result

PR-02 Data Catalog v3 acceptance passed for the implementation and the local BTC futures source. The v1/v2 catalog code and schemas remain available, and the repository’s active governance verification passed. The report does not authorize merging, pushing, or opening the statistical inference gate.

## Real BTC 5m evidence

Source: `/home/von/workspace/code/coin/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-5m-futures.feather`  
Catalog output: `/tmp/alpha-lab-data-catalog-v3-acceptance-final`  
Rows: `503607`  
Observed range: `[2022-01-01T00:00:00Z, 2026-10-15T15:10:00Z]`  
Monthly partitions: `58`  
Discovery: `AVAILABLE`  
Source SHA-256: `f39767aefb7f9e2b6651912a1e84b85fef3ac6ada8a6026eeb153d93914d589c`  
Manifest fingerprint: `b5bfd7dccd04b337035332cc6c3e94f4a3088c2cd9a13da789f2958bb6783ddb`  
Repeated registration returned the same fingerprint.

| Requested interval `[start, end)` | Observed | Missing | Status |
|---|---:|---:|---|
| 2025-01-01T00:00:00Z to 2026-01-01T00:00:00Z | 105120 | 0 | AVAILABLE |
| 2026-10-01T00:00:00Z to 2026-10-15T15:15:00Z | 4215 | 0 | AVAILABLE |

The second interval ends one 5-minute timeframe after the latest observed candle. The source file was read-only throughout acceptance. The first real-data run exposed a timestamp resolution mismatch; coverage and fingerprinting were corrected to normalize timestamps to nanoseconds, and the acceptance was rerun successfully.

## Synthetic and compatibility evidence

The contract and CLI fixtures cover invalid and naive timestamps, non-UTC offsets, timeframe alignment, duplicates, missing candles, OHLCV bounds and values, sparse Funding/OI behavior, source hashes, partition fingerprints, exclusive ends, and CLI routing. Coverage tests include full, leading, trailing, internal, unknown-dataset, and bounded gap-list cases.

The legacy v1/v2 tests and standalone package checks ran with the v3 suite. The `data_engineering` wheel contains both quality contract modules and `dataset-manifest-v3.schema.json`; installing the wheel into `/tmp` successfully imported `data_engineering.quality.contracts` and loaded the packaged schema.

## Commands and results

Repository regression and catalog tests:

```text
env PYTHONPATH=/tmp/alpha-catalog-v3-test-deps /home/von/workspace/venv/bin/python -m pytest -o addopts='' -o cache_dir=/tmp/pytest-alpha-catalog-v3-final -q alpha_lab_contracts/tests/ alpha_lab/tests/ alpha_lab/calibration/ tests/test_data_engineering.py tests/test_data_engineering_packaging.py tests/test_data_engineering_catalog_v3.py tests/test_data_engineering_catalog_v3_cli.py tests/test_cross_sectional_fundamental_runner.py tests/test_fundamental_asof.py tests/test_fundamental_feature_bridge.py tests/test_fundamental_registries.py tests/test_fundamentals.py tests/test_pit_audit.py tests/test_pit_evidence.py data_engineering/scripts/test_fundamental_coverage_audit.py
502 passed, 76 warnings in 19.48s
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
Successfully built alpha-lab-data-engineering-0.1.0
```

Wheel install smoke:

```text
/home/von/workspace/venv/bin/python -m pip install --no-deps --target /tmp/alpha-catalog-v3-installed /tmp/alpha-catalog-v3-wheel/alpha_lab_data_engineering-0.1.0-py3-none-any.whl
env PYTHONPATH=/tmp/alpha-catalog-v3-installed /home/von/workspace/venv/bin/python -c 'from importlib.resources import files; import data_engineering.quality.contracts; schema=files("data_engineering").joinpath("schemas","dataset-manifest-v3.schema.json"); print({"quality_import":True,"v3_schema_packaged":schema.is_file()})'
{'quality_import': True, 'v3_schema_packaged': True}
```
