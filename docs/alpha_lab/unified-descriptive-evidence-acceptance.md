# Unified Descriptive Evidence Acceptance

**Source implementation HEAD:** `66c4101fc5`  
**Overall:** PASS  
**Formal statistical inference:** BLOCKED

The machine-readable record is [`unified-descriptive-evidence-acceptance.json`](unified-descriptive-evidence-acceptance.json). Real Binance data, synthetic multi-asset data, and history-store checks are recorded separately.

## Real TS data

The v2 service processed Binance Vision USD-M `BTCUSDT` 5m data for May 2025. The source archive SHA-256 is `073da284f178e72777abbfbcf79faa99fcf77bd69c4a678938abbf6cd7d14f5b`; the Catalog dataset ID is `binance-futures-btc-ohlcv-5m-6c7cb41c78f4`. The dataset manifest SHA-256 is `97c4b97c6898af64faa114b943af7dfae0eba6debe1f34fb241d2f995d4fc62f`, and its fingerprint is `f965a8385bd584496fc4bfb54fc779acda9250d7290e8eff1b57ffbbb7e299c6`.

The authorized run evaluated 8,928 timestamps across 18 chunks. It returned `EXPLORATORY`, with 8,925 valid labels (99.9664% coverage), Pearson IC `-0.03565`, and RankIC `-0.04453`. Monthly and yearly summaries both contain IC and RankIC. Factor turnover is reported descriptively. The cost diagnostic uses an explicit 10 bps per side assumption and is not executable PnL.

The forced-restart acceptance produced no partial Evidence. Resumed and sequential stateful chunk output/checkpoint hashes matched, and stateless/stateful factor values matched. The full temporary report SHA-256 is recorded in the JSON file; raw data and temporary Catalog products remain outside the repository.

## Synthetic CS data

The authorized service integration fixture contains 8 timestamps and 5 symbols (40 factor rows). The service emitted a cross-sectional report with 8 effective sections, 5 assets per section, five quantile groups per section, and mean CS RankIC `1.0`. This deterministic fixture validates dispatch and report shape; it is not a real-market performance claim.

## Evidence, history, and security

Evidence binds Campaign/policy identity, ResearchRequest digest, mode, Dataset manifest/fingerprint/source provenance, source SHA-256, canonical parameters, Worker image digest, evaluator identity, output hashes, and the descriptive report. Replaying identical work returns the same Evidence hash; conflicting content is rejected. `NOT_EVALUABLE` reports may be stored after complete execution. Incomplete or interrupted tasks cannot publish Evidence.

The read-only history facade merges configured legacy, Python Factor Service v1, and v2 summaries, tags each record with Store/protocol provenance, and reports unavailable configured Stores explicitly. Tests hash all three temporary SQLite files before and after reads; their bytes remain unchanged. Source code is not returned in history. No formal inference fields are exposed.

The separate PR-05 container acceptance covers Network None, no Docker Socket, readonly root, protected path nonaccess, and runtime resource limits. This PR adds no path or data-write capability to MCP.

## Verification

Real data acceptance:

```bash
/home/von/workspace/venv/bin/python -m alpha_lab_service.acceptance_v2 --archive /tmp/BTCUSDT-5m-2025-05.zip --checksum /tmp/BTCUSDT-5m-2025-05.zip.CHECKSUM --output-dir /tmp/pr06-ts-acceptance-final
```

Synthetic CS acceptance:

```bash
/home/von/workspace/venv/bin/python -m pytest alpha_lab_service/tests/test_v2_recovery.py::test_cross_sectional_v2_service_emits_cs_report -q
```

History read-only and source failure handling:

```bash
/home/von/workspace/venv/bin/python -m pytest alpha_lab_service/tests/test_history_v2.py -q
```

The frozen `alpha_lab/freeze-manifest-v1.1.json` content remains at SHA-256 `692eda36c07f9dfd113dc57d470f7db15507fc75321bc5a79bf5edf9ff9561c2`. Final regression and governance commands/results are recorded in the PR-06 implementation ledger and final acceptance JSON.

Final local regression at source implementation HEAD `66c4101fc5`:

- `ALPHA_LAB_WORKER_IMAGE=sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced /home/von/workspace/venv/bin/python -m pytest alpha_lab_service/tests -q` — 148 passed, 2 existing pytest configuration warnings.
- `/home/von/workspace/venv/bin/python -m pytest alpha_lab_contracts/tests -q` — 60 passed, 2 existing pytest configuration warnings.
- `/home/von/workspace/venv/bin/python -m pytest alpha_lab_service/tests/test_ci_protection.py -q` — 6 passed, 2 existing pytest configuration warnings.
- `/home/von/workspace/venv/bin/python -m alpha_lab_service.governance` — valid for governance v1.4, service v3, core v1.2, and historical core v1.1.
- `git diff --check` — PASS.

## Acceptance limits

PR-06 local acceptance is complete. Remote GitHub branch protection and required checks remain unverified because the connected integration cannot read those settings. Production host identity separation and durable Data Maintainer permissions require deployment evidence. PR-07 Agent request/coverage tools and its end-to-end flow remain separate roadmap work. Formal statistical inference stays blocked.
