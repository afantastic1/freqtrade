# Python Factor Service v2 acceptance

## Reproduce

The acceptance used the Binance Vision UM monthly archive for BTCUSDT 5-minute
klines, May 2025. The runner checks the archive against the published checksum
sidecar, converts it into a temporary source-root Feather file, registers that
file in a temporary Catalog v3, and builds the dataset through the trusted
`DatasetBuilderV2` and Campaign policy. All generated data, Catalog entries,
Stores and result files remain under the selected temporary output directory.

```bash
curl -fSL -o /tmp/BTCUSDT-5m-2025-05.zip \
  https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2025-05.zip
curl -fSL -o /tmp/BTCUSDT-5m-2025-05.zip.CHECKSUM \
  https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2025-05.zip.CHECKSUM

ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced \
  python -m alpha_lab_service.acceptance_v2 \
  --archive /tmp/BTCUSDT-5m-2025-05.zip \
  --checksum /tmp/BTCUSDT-5m-2025-05.zip.CHECKSUM \
  --output-dir /tmp/alpha-lab-v2-acceptance
```

The same image can be checked independently with:

```bash
ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced \
  python -m pytest alpha_lab_service/tests -q
```

The first command writes machine-readable output and all chunk input/output and
checkpoint hashes to `acceptance-v2.json`. The full result from this run is
committed alongside this report.

## Recorded result

- Status: `PASS`; v2 Evidence status: `DIAGNOSTIC`.
- Archive SHA-256: `073da284f178e72777abbfbcf79faa99fcf77bd69c4a678938abbf6cd7d14f5b`.
- Registered Catalog dataset: `binance-futures-btc-ohlcv-5m-6c7cb41c78f4`;
  source manifest SHA-256: `4345abfbc69845336fc8abe4a99e2763ea06b4897fa8ca02ee883457fde10375`.
- Dataset manifest SHA-256:
  `97c4b97c6898af64faa114b943af7dfae0eba6debe1f34fb241d2f995d4fc62f`;
  dataset fingerprint:
  `f965a8385bd584496fc4bfb54fc779acda9250d7290e8eff1b57ffbbb7e299c6`.
- Authorized schedule: 8,928 decision steps, 18 chunks. Plan hash:
  `fcc71a53e9013efd986ef6d6d3891ec9b8665ebc56ae89123cd73e79e22d9bb7`.
- Worker image:
  `sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced`.
- Forced restart produced no partial Evidence. Resumed stateful output hashes
  and checkpoint hashes matched a clean sequential stateful run. Stateless and
  stateful factor values matched exactly.
- Wall time: 76.78 seconds stateless, 123.40 seconds for the stateful resume
  phase after the forced interruption, and 127.54 seconds sequential stateful.
- The descriptive evaluator reported 8,925 valid rows. Three terminal
  forward-return labels lacked the required future horizon beyond the May
  archive. One-symbol time-series data has no cross-sectional IC sections, so
  the final Evidence is correctly `DIAGNOSTIC`.
- Formal inference remains `BLOCKED`.

The actual-container v2 suite passed all five tests. It exercised network and
Docker-socket denial, protected path absence, read-only root, tmpfs isolation,
PID and memory limits, timeout cleanup, malformed frame rejection and a
missing-image failure. The existing v1 container tests also use the same
committed runtime profile. Two pytest-asyncio configuration warnings are
present in this environment and do not affect the result.

## Release boundary

This is a local code and Docker-daemon acceptance. It does not prove remote
branch protection, deployment OS account separation, or that a production MCP
identity cannot access a production Docker socket or protected host data.
Formal inference stays blocked. The broader general research service must stay
blocked until those host-level controls are separately verified.
