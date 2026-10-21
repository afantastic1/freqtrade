# Dynamic dataset builder acceptance evidence

This report records PR-04 acceptance for code HEAD `a2b383a803`. Synthetic tests
are separated from builds using the local registered Feather sources.

## Automated evidence

- `tests/test_data_engineering_catalog_v3.py`: pinned Catalog v3 snapshots, source digest verification, and half-open reads.
- `alpha_lab/tests/test_availability.py`: source evidence, fixed-delay availability, point-in-time joins, and staleness.
- `alpha_lab/tests/test_temporal_plan.py`: split-bounded warmup/labels, missing slots, and embargo.
- `alpha_lab/tests/test_build_products.py`: worker/evaluator product separation.
- `alpha_lab/tests/test_dataset_manifest.py`: deterministic fingerprints and immutable publication.
- `alpha_lab/tests/test_source_resolver.py` and `alpha_lab/tests/test_dataset_builder_ts.py`: exact identity resolution and time-series construction.
- `alpha_lab/tests/test_dataset_builder_cs.py`: point-in-time universe membership and cross-sectional labels.
- `alpha_lab/tests/test_dataset_cli.py`: legal 2025/2026 date ranges, trusted authorization, and legacy CLI compatibility.
- Synthetic acceptance suite: 68 passed.
- Compatibility suite (legacy loaders/panel CLI, Catalog v3, and service manifest): 115 passed.
- Alpha Lab/contracts/service suites: 496 passed; 10 worker-service tests could not start because `ALPHA_LAB_WORKER_IMAGE` is not set to a locally built immutable image ID.
- `compileall`, core v1.2, governance v1.4, service v3, and `alpha_lab_service.governance`: passed.

The synthetic set includes `test_builder_ts_rejects_wholly_missing_primary_source_in_preserve_mode`,
late-entry availability checks, stable evidence-row selection after interval
slicing, dynamic-universe perturbation and cumulative listing-age tests,
historical membership at decision time, auxiliary availability scoped to worker
rows, unaligned timeframe-grid requests, missing decision-boundary tests, and
publication integrity regressions. A source with no observations in the
requested interval is rejected even when the row-level missing-data policy is
`preserve`.

## Actual-data evidence

All builds below read local Freqtrade Feather files without modifying them.
Source rows are verified against Catalog v3 partition and manifest hashes.

| Build | Worker rows | Evaluator rows | Missing labels | Build fingerprint |
|---|---:|---:|---:|---|
| BTC futures 5m, 2026-10-01 to 2026-10-02 | 300 (12 warmup + 288 study) | 288 | 0 | `6b00c26ddb468f480ed5a970726a3eef876e5ab16acbf077c5d270138feffac3` |
| BTC futures 1h, 2025 | 8,759 | 8,759 | 3 | `061d3f3898662e26cbf9bcc1e7d62c4aeb4b1fdaa32ba53f653920312b34c2ed` |
| ETH futures 1h, 2025 | 8,759 | 8,759 | 3 | `5aee4a78a2ce0d290f5aa17e43a97ad6f47ff964432043722a4f5a4181d10fd6` |
| BTC/ETH futures 1h cross-section, 2025 | 17,518 | 17,518 | 6 | `f283b904091aa4fd39ea17afc87f19a1bcc14fdf17d3cbd33e50d0d888c7bc00` |
| BTC/ETH/SOL/BNB/XRP futures 1h cross-section, 2022–2024 | 131,515 | 131,515 | 15 | `cab6ce0b75589a23db95cc44e14fa2b05c0f1f77f90ec0e499f8d3d1a068f3da` |

Validation and discovery builds are clipped to their authorized split boundary.
The split-start candle needed to create the first in-range hourly decision is
outside the selected split, so each symbol has one `MISSING_DECISION_SLOTS`
entry. With `preserve`, three horizon-dependent labels per symbol remain null
and are reported as `LABEL_EXTENSION_TRUNCATED_AT_SPLIT`; 24 warmup bars are
also unavailable at the split start and reported. No adjacent split data is
read. The historical panel uses an explicit fixed universe and records its
survivorship caveat.

All requests use acceptance policy SHA-256
`b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f`.
Request SHA-256 values are BTC 2026 5m
`73136249ed3d0a411575b01003f6fb4b77ed709b2188cd8fe1ecafdb9414922e`, BTC
2025 1h `7bc7ad0afadd888915eef3a15a0320df185ba5118c9886dcea2228bf8a1b7323`,
ETH 2025 1h `cfc1626085180c12f1d6cda47afd54b38401fe52c6e6f255cbc08a6f2c08e7ee`,
BTC/ETH 2025 cross-section
`a972b151bb32faafd8428977378c3d93961a0a9c03eeece2d6dededcf59891ad`, and
five-asset 2022–2024 cross-section
`9af17e1805ae921dc88d86372631a83a0c6a7c602de12606933024b301486ed4`.

The five builds were rerun against code HEAD `a2b383a803` with
`/home/von/workspace/venv/bin/python -m alpha_lab.dataset_cli dataset build`
using request files `/tmp/pr04_btc_2026_request.json`,
`/tmp/pr04_btc_2025_1h_request.json`, `/tmp/pr04_eth_2025_1h_request.json`,
`/tmp/pr04_btc_eth_2025_cs_1h_request.json`, and
`/tmp/pr04_five_asset_2022_2024_1h_request.json`; Catalog roots were
`/tmp/alpha-catalog-v3-review-final-acceptance` for BTC 5m and
`/tmp/pr04-acceptance-1h-catalog` for 1h, with source root
`binance-futures=/home/von/workspace/code/coin/freqtrade/user_data/data/binance/futures`.
Cross-sectional requests used `--universe fixed`; 2025 and discovery builds
used `--missing-policy preserve`; the BTC 2026 build used `reject`.

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
service suite's ten image-gated tests remain unverified because
`ALPHA_LAB_WORKER_IMAGE` is not configured as an immutable local image ID.
