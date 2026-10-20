# Dynamic dataset builder acceptance evidence

This report records the PR-04 acceptance commands and results. Synthetic tests
are listed separately from checks using registered source data; no real BTC
2026 or ETH/BTC 2025 source archive is claimed by the unit-test results.

## Automated evidence

- `alpha_lab/tests/test_data_engineering_catalog_v3.py`: pinned Catalog v3 snapshots, source digest verification, and half-open reads.
- `alpha_lab/tests/test_availability.py`: source evidence, fixed-delay availability, point-in-time joins, and staleness.
- `alpha_lab/tests/test_temporal_plan.py`: split-bounded warmup/labels, missing slots, and embargo.
- `alpha_lab/tests/test_build_products.py`: worker/evaluator product separation.
- `alpha_lab/tests/test_dataset_manifest.py`: deterministic fingerprints and immutable publication.
- `alpha_lab/tests/test_source_resolver.py` and `alpha_lab/tests/test_dataset_builder_ts.py`: exact identity resolution and time-series construction.
- `alpha_lab/tests/test_dataset_builder_cs.py`: point-in-time universe membership and cross-sectional labels.
- `alpha_lab/tests/test_dataset_cli.py`: legal 2025/2026 date ranges, trusted authorization, and legacy CLI compatibility.

## Actual-data evidence

The integration tests build from a local Feather fixture registered in Catalog
v3. External BTC 2026 and ETH/BTC 2025 acceptance archives and historical
2022–2024 reproduction data were not available in this checkout; those claims
remain unverified until the pinned source snapshots are supplied.

## Limitations

The current integration path validates and records Funding/OI source identity
and availability metadata. Full auxiliary event feature joins and a real-data
acceptance run remain part of the PR-04 completion gate and are not inferred
from the synthetic tests listed above.
