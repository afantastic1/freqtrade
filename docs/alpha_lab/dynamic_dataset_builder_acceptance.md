# Dynamic dataset builder acceptance evidence

This report records PR-04 acceptance for code HEAD `887066727c`. Synthetic tests
are separated from builds using the local registered Feather sources.

## Automated evidence

- `alpha_lab/tests/test_data_engineering_catalog_v3.py`: pinned Catalog v3 snapshots, source digest verification, and half-open reads.
- `alpha_lab/tests/test_availability.py`: source evidence, fixed-delay availability, point-in-time joins, and staleness.
- `alpha_lab/tests/test_temporal_plan.py`: split-bounded warmup/labels, missing slots, and embargo.
- `alpha_lab/tests/test_build_products.py`: worker/evaluator product separation.
- `alpha_lab/tests/test_dataset_manifest.py`: deterministic fingerprints and immutable publication.
- `alpha_lab/tests/test_source_resolver.py` and `alpha_lab/tests/test_dataset_builder_ts.py`: exact identity resolution and time-series construction.
- `alpha_lab/tests/test_dataset_builder_cs.py`: point-in-time universe membership and cross-sectional labels.
- `alpha_lab/tests/test_dataset_cli.py`: legal 2025/2026 date ranges, trusted authorization, and legacy CLI compatibility.
- Synthetic acceptance suite: 50 passed.
- Compatibility suite (legacy loaders/panel CLI, Catalog v3, and service manifest): 115 passed.
- Alpha Lab/contracts/service suites: 478 passed; 10 worker-service tests could not start because `ALPHA_LAB_WORKER_IMAGE` is not set to a locally built immutable image ID.
- `compileall`, core v1.2, governance v1.4, service v3, and `alpha_lab_service.governance`: passed.

The synthetic set includes `test_builder_ts_rejects_wholly_missing_primary_source_in_preserve_mode`;
a source with no observations in the requested interval is rejected even when
the row-level missing-data policy is `preserve`.

## Actual-data evidence

All builds below read local Freqtrade Feather files without modifying them.
Source rows are verified against Catalog v3 partition and manifest hashes.

| Build | Worker rows | Evaluator rows | Missing labels | Build fingerprint |
|---|---:|---:|---:|---|
| BTC futures 5m, 2026-10-01 to 2026-10-02 | 300 (12 warmup + 288 study) | 288 | 0 | `83d7b9870dd3f41b4d3d7ab4b6b7b2188c7f56b09be4b9c37436a6abc9ad058c` |
| BTC futures 1h, 2025 | 8,760 | 8,760 | 4 | `2362f8ec66ffba4ed59c7b9a1a0d81ce8e398e89a062063975dd8a96258d58a7` |
| ETH futures 1h, 2025 | 8,760 | 8,760 | 4 | `7a6f62d5c95abf279d72d69ddc709f011f1d1cfbfd4fb129cf1fa090575c9a7a` |
| BTC/ETH/SOL/BNB/XRP futures 1h, 2022–2024 | 131,520 | 131,520 | 20 | `0705bd0b27c0c48b0bf0874c4a8bb9e55e9d12966942fea9b38759a25c9258c4` |

The three validation/discovery builds end at their authorized split boundary.
With `preserve`, the four horizon-dependent labels per symbol at each split end
remain null and are reported as `LABEL_EXTENSION_TRUNCATED_AT_SPLIT`; no adjacent
split data is read. The historical panel uses an explicit fixed universe and
records its survivorship caveat.

The BTC 2026 build used Catalog dataset
`binance-futures-btc-ohlcv-5m-6c7cb41c78f4`, manifest pin
`f7478bbe08a2a6bb8da2af12f63aa84572b0e2f825ae50b86cb8ec5923614f12`, and source
SHA-256 `f39767aefb7f9e2b6651912a1e84b85fef3ac6ada8a6026eeb153d93914d589c`.
The 1h builds used pins BTC `2dcb9cb9fc72b124d48dc92bdaf1d0aa2460651294a6b1487e7e83d767bf79f2`,
ETH `a1b5d5279def3a65d1b15648ee8e9519e3abf2e05da0142151764ed3afe66eb0`,
SOL `06a28278061fda1ed63b02340a4a546082ca7227896d785c5ac1337b54906667`,
BNB `8d37686f5ef077cb1067cd4c7503aafce7071a21f66ad038a047ac9a8f53458e`, and
XRP `81554709d27fe038fca2c41967ab39c1798ca9bdf6275755f67e5413d5f21aca`.

## Limitations

Funding, Open Interest, Spot, and Futures auxiliary inputs have synthetic
availability-time join tests, including late publication and the exact
availability boundary. The future-perturbation check compares as-of worker
frames; it does not run a downstream factor against those products. The broad
service suite's ten image-gated tests remain unverified in this environment.
