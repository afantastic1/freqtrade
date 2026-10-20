# Alpha Lab five-asset Panel reproducibility report

**Panel build:** PASS  
**Research run:** NOT RUN  
**Panel-level admission:** `DATA_PANEL_REPRODUCIBLE`  
**Source/Agent process isolation:** separate gate; not established by this build

## Fixed panel contract

- Market: Binance USDⓈ-M USDT perpetual Futures OHLCV.
- Assets: `BTC/USDT:USDT`, `ETH/USDT:USDT`, `SOL/USDT:USDT`, `BNB/USDT:USDT`, `XRP/USDT:USDT`.
- Timeframe: 1h.
- Open-time and availability interval: `[2022-01-01T00:00:00Z, 2025-01-01T00:00:00Z)`.
- Source: existing Freqtrade Futures Feather files, loaded with `alpha_lab.data.loader.load_ohlcv`; no download, interpolation, missing-candle fill, factor transformation, or research evaluation.
- Time semantics: Freqtrade `date` is candle open; close is open plus one hour; `available_time` is close plus the loader's one-second assumed delay. This delay is a declared assumption, not measured publication latency.
- Rows are retained only if both `open_time < 2025-01-01` and `available_time < 2025-01-01`. The final 2024-12-31 23:00 candle is excluded because its assumed availability is 2025-01-01 00:00:01 UTC.

## Reproduction

Retained read-only panel:

```text
user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv
user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv.manifest.json
```

Rebuild command (Maintainer data preparation only):

```text
.venv/bin/python scripts/alpha_lab_admission_gate.py build-panel \
  --output user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv
```

Independent reproduction was built at `/tmp/alpha-lab-panel-admission-repro/usdm_1h_2022_2024.csv`. Both outputs were byte-identical. The builder SHA-256 recorded in the manifest is `04fc6f1564a0ed32ec73a06adbe688b6dd2c514a59c27d22f2749e005d464332`. The same command verifies source hashes, builder hash, panel hash, interval, symbols, content fingerprint, row counts, nulls, complete five-asset sections, and read-only modes:

```text
.venv/bin/python scripts/alpha_lab_admission_gate.py verify-panel \
  --manifest user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv.manifest.json
```

## Artifact identity

| Item | Value |
|---|---|
| Panel SHA-256 | `20991661435cd88cf6c2ccf3d128693169bce5f30e3f70f4412b2aa3e1fc88f8` |
| Research `MarketPanel.fingerprint()` after CSV reload | `4bb720b9160bc17d49094b09d020c52f41179123c93d5e8b9e3231fe2cd134d6` |
| Rows | 131,515 (26,303 per asset) |
| UTC sections | 26,303, each with all five assets |
| Each asset's first/last open | 2022-01-01 00:00 / 2024-12-31 22:00 UTC |
| Internal missing bars | 0 per asset; 100% coverage between first and last observed bar |
| Required OHLCV/time null values | 0 |
| File permissions | panel and manifest set to `0444`; an OS worker must mount them read-only |

The per-source SHA-256 values, sizes, row counts, quality summaries, timestamp semantics, builder hash, panel hash, and content fingerprint are in the [Panel Manifest](../../user_data/research/alpha_lab/panels/usdm_1h_2022_2024.csv.manifest.json). The SHA-256 values before/after each build were equal; no source file changed. The local Research Store hash was also unchanged.

## Data provenance and boundaries

The five source Feather files are mixed-year files. The trusted Maintainer preparation process calls the existing loader, which can open/read source data before applying the time filter. This build therefore does not claim that the Maintainer process never read 2025+ source rows. The resulting Panel contains only bounded rows and has no raw 2025+ observations. A research worker must receive only this bounded file read-only and must not mount the raw Futures directory. Agent-process enforcement remains blocked in [the isolation audit](agent_isolation_audit.md).

The fixed universe is not a survivorship-safe historical universe. The panel proves observed rows and coverage for these five current contracts; it does not prove historical listing membership or tradeability. No research Evidence or Decision was produced.
