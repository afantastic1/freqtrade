# Findings

- Branch: my_dev; initial HEAD 9d52ebe924a14641b08e3d8c6e43892418a4be40; clean.
- server.py already provides exactly seven Agent tools; runtime.py accepts operator-owned dependency bundles.
- jobs_v2.py, request registry, store, catalog gateway, policy registry and broker client are reusable.
- Worker image profile pins sha256:ac09f79dc85832d1cbe09a0efc9a14f210552d0479aefdb4b3be87ef72f9dced.
- BTC Feather exists at user_data/data/binance/futures/BTC_USDT_USDT-5m-futures.feather (12,265,738 bytes).
- PR09 Catalog exists under /tmp/alpha-lab-pr09/catalog-v3; map the fixed source root into the service container read-only.
- dataset_builder_acceptance Discovery ends at 2025-01-01; never read 2025 or 2026 samples in this run.
- Docker CLI currently cannot reach /var/run/docker.sock without escalation.
- Existing root planning files belong to earlier work and are tracked; this task uses an isolated planning directory.
- The actual normalized dataset root has only `year=2022`, `year=2023`, and `year=2024`; the local startup check now requires exactly these partitions.
- The actual Catalog has one BTC dataset identity, 5m. The Campaign also authorizes 1h, but no matching 1h Catalog registration exists here; 15m/4h are likewise not registered/authorized.
- Ruling: Use new service v5, governance v1.6, and active core boundary v1.3 to protect the local Factory while retaining historical v1.2 hashes. If this boundary versioning is wrong, CI and code ownership must still remain tied to these active manifests.
- Docker daemon reports Registry Mirror `https://gpu34ekhgwm14ghgur.xuanyuan.run/`; host shell HTTPS proxy reaches GHCR, but the daemon pull of `ghcr.io/astral-sh/uv:0.11.19` stalled. Avoided a machine-wide daemon proxy change.

