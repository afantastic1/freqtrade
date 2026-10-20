# Data Catalog v3 source preparation acceptance

The source CLI is an operator tool. It only reads explicitly selected roots and writes to explicitly selected raw, normalized, catalog, and journal locations. It has no MCP, Agent, web, worker, or campaign execution hooks.

All timestamps are timezone-aware UTC and intervals use `[start, end)`. Local sources are read-only Freqtrade Feather files. Binance Vision supports the reviewed USD-M crypto symbol/timeframe allowlist, and archive downloads require the checksum sidecar to validate first. Use `--allow-1m` only for the supported BTC/ETH 1m experiment.

## Local source workflow

Discover metadata and exact bounded monthly artifacts:

```sh
python -m data_engineering sources discover \
  --adapter local --source-root binance=user_data/data/binance \
  --catalog user_data/data_engineering/catalog-v3 \
  --exchange binance --market-type futures --asset BTC \
  --exchange-symbol 'BTC/USDT:USDT' --data-kind ohlcv --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z \
  --request-id btc-futures-2025-01
```

Inspect normalized coverage without downloads or writes:

```sh
python -m data_engineering sources plan \
  --adapter local --source-root binance=user_data/data/binance \
  --catalog user_data/data_engineering/catalog-v3 \
  --normalized-root user_data/data_engineering/normalized --dry-run \
  --exchange binance --market-type futures --asset BTC \
  --exchange-symbol 'BTC/USDT:USDT' --data-kind ohlcv --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z \
  --request-id btc-futures-2025-01
```

Validate, then publish a bounded artifact. Publication rechecks the receipt and source bytes before adding an immutable Parquet object and append-only Catalog v3 snapshot:

```sh
python -m data_engineering sources validate \
  --adapter local --source-root binance=user_data/data/binance \
  --catalog user_data/data_engineering/catalog-v3 \
  --exchange binance --market-type futures --asset BTC \
  --exchange-symbol 'BTC/USDT:USDT' --data-kind ohlcv --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z \
  --request-id btc-futures-2025-01

python -m data_engineering sources publish \
  --adapter local --source-root binance=user_data/data/binance \
  --catalog user_data/data_engineering/catalog-v3 \
  --normalized-root user_data/data_engineering/normalized \
  --exchange binance --market-type futures --asset BTC \
  --exchange-symbol 'BTC/USDT:USDT' --data-kind ohlcv --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z \
  --request-id btc-futures-2025-01
```

`sync` runs the supported stages and resumes from the per-artifact journal under `<catalog>/source-journal`. Use `--month YYYY-MM` to select one discovered monthly artifact. Local Feather download is deliberately unsupported. The read-only `plan` command requires both `--normalized-root` and `--dry-run` so it can verify existing normalized coverage without writing.

## Binance Vision workflow

Vision commands require `--raw-root` for download and sync. `discover` and `plan` perform bounded metadata discovery only. `download` fetches checksum metadata before archive bytes; `sync` downloads, validates, and publishes in one local operator invocation. Provide the same dataset selection and bounds shown above, with `--adapter vision`, `--raw-root user_data/data_engineering/raw/binance-vision`, and `--normalized-root user_data/data_engineering/normalized` for planning and publication.

## Acceptance status

- Automated adapter, pipeline, catalog, publisher, CLI, and legacy importer regressions are run in the repository test environment.
- Bounded live Vision acceptance completed on 2026-10-20 for BTCUSDT 1h, September 2026. Archive URL: `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip`. The checksum sidecar reported SHA-256 `07ab960882feb6d3120673a851f2a654c4c3f31f3a74d1f3fc4b0351a8ee1483`. Converter: `data-engineering-parquet-v1`. Normalized fingerprint: `49a9e1b565e877b3c75bd8ad20c232f65dd9054c9f06fcc6ccce8811ad107b18`. Stage statuses: `AVAILABLE` discovery, `DOWNLOADED` with checksum verified, `VALIDATED` (720 rows), `PUBLISHED`, and final Catalog v3 `AVAILABLE` with no issues. The object and test catalog are under `/tmp/pr03-vision-acceptance`.
- The live acceptance exposed an undefined S3 response-size constant and a journal path loss between Vision validation and publication. Both were fixed and covered by regressions before the acceptance was repeated successfully.
