"""Read-only acceptance checks for the local v0.1 fundamental catalog."""

from __future__ import annotations

from pathlib import Path

from .fundamentals import FundamentalDataAdapter
from .scripts.fundamental_coverage_audit import build_report


def audit_local_catalog(root: str | Path) -> dict:
    """Validate hashes, coverage, entity mappings, and both PIT access modes."""
    root = Path(root)
    coverage = build_report(root)
    if coverage["errors"]:
        raise ValueError(f"Catalog validation errors: {coverage['errors']}")
    if coverage["snapshot_manifest_count"] != 14:
        raise ValueError("expected all 14 local snapshot manifests")
    if coverage["feature_series_count"] != 30:
        raise ValueError("expected all 30 registered metric series")
    if coverage["present_expected_dataset_count"] != 14:
        raise ValueError("one or more expected fundamental datasets are missing")
    if set(coverage["assets_with_registered_snapshots"]) != {
        "BTC", "ETH", "SOL", "BNB", "XRP"
    }:
        raise ValueError("five-asset economic mapping is incomplete")

    adapter = FundamentalDataAdapter(root)
    snapshots = adapter.list_snapshots()
    if len(snapshots) != 14:
        raise ValueError("adapter did not enumerate all 14 fundamental snapshots")
    pins = [(item.dataset_id, item.snapshot_sha256) for item in snapshots]
    exploratory = adapter.read_many(pins, access="exploratory")
    if exploratory.empty or not exploratory.pit_status.eq("UNVERIFIED").all():
        raise ValueError("local historical fundamentals must remain UNVERIFIED")
    if exploratory.available_time.notna().any():
        raise ValueError("unverified source timestamps entered effective availability")

    strict_rejected = 0
    for dataset_id, digest in pins:
        try:
            adapter.read(dataset_id, digest, access="strict_pit")
        except ValueError as exc:
            if "strict_pit rejects" not in str(exc):
                raise
            strict_rejected += 1
    if strict_rejected != 14:
        raise ValueError("strict PIT did not reject every unverified snapshot")

    series = coverage["metrics"]
    snapshot_hashes = sorted({
        (item["dataset_id"], item["sha256"]) for item in series
    })
    if len(snapshot_hashes) != 14:
        raise ValueError("expected 14 distinct dataset/snapshot hash pins")
    series_keys = {(item["asset"], item["entity_scope"], item["metric"])
                   for item in series}
    chain_names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
                   "BNB": "BSC", "XRP": "XRPL"}
    expected_entities = {
        (ticker, "token", f"token:{ticker}") for ticker in chain_names
    } | {
        (ticker, "ecosystem", f"ecosystem:{chain}")
        for ticker, chain in chain_names.items()
    }
    economic_entities = sorted({
        (item["asset"], item["entity_scope"], item["asset_id"])
        for item in series
    })
    if not expected_entities.issubset(set(economic_entities)):
        raise ValueError("token/ecosystem economic identity mapping is incomplete")
    expected_chain_entities = {
        (ticker, "chain", f"chain:{chain}")
        for ticker, chain in (("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("XRP", "XRPL"))
    }
    if not expected_chain_entities.issubset(set(economic_entities)):
        raise ValueError("available chain metric identities do not match registry")
    if ("SOL", "chain", "AdrActCnt") in series_keys or (
        "BNB", "chain", "AdrActCnt"
    ) in series_keys:
        raise ValueError("missing SOL/BNB active-address series appeared in Catalog")
    xrp_tvl = [item for item in series if item["asset"] == "XRP"
               and item["metric"] == "chain_tvl_usd"]
    if len(xrp_tvl) != 1 or xrp_tvl[0]["first_date"] != "2024-03-23":
        raise ValueError("XRP TVL pre-listing coverage differs from audited Catalog")
    for metric in ("ecosystem_daily_fees_usd", "ecosystem_daily_revenue_usd"):
        solana = [item for item in series if item["asset"] == "SOL"
                  and item["metric"] == metric]
        if len(solana) != 1 or solana[0]["missing_at_start"] <= 0:
            raise ValueError(f"Solana {metric} 2022 coverage was filled or is absent")

    return {
        "status": "PASS",
        "read_only": True,
        "development_window": ["2022-01-01", "2025-01-01"],
        "snapshot_manifest_count": coverage["snapshot_manifest_count"],
        "verified_snapshot_hash_count": len(snapshot_hashes),
        "snapshot_hashes": [
            {"dataset_id": dataset_id, "sha256": digest}
            for dataset_id, digest in snapshot_hashes
        ],
        "asset_count": len(coverage["assets_with_registered_snapshots"]),
        "metric_series_count": coverage["feature_series_count"],
        "exploratory_rows": len(exploratory),
        "pit_statuses": sorted(exploratory.pit_status.unique().tolist()),
        "strict_rejected_snapshot_count": strict_rejected,
        "entity_assets": coverage["assets_with_registered_snapshots"],
        "economic_entities": [
            {"asset": asset, "entity_scope": scope, "asset_id": asset_id}
            for asset, scope, asset_id in economic_entities
        ],
        "coverage_metrics": [
            {key: item[key] for key in (
                "dataset_id", "asset", "asset_id", "entity_scope", "metric", "rows",
                "first_date", "last_date", "coverage_pct", "missing_at_start",
                "internal_missing_days", "missing_at_end", "sha256",
            )}
            for item in series
        ],
        "missing_series": [
            {"asset": asset, "metric": "AdrActCnt", "entity_scope": "chain"}
            for asset in ("SOL", "BNB")
        ],
        "xrp_tvl_first_date": xrp_tvl[0]["first_date"],
        "solana_fees_missing_at_start_days": next(
            item["missing_at_start"] for item in series
            if item["asset"] == "SOL" and item["metric"] == "ecosystem_daily_fees_usd"
        ),
        "solana_revenue_missing_at_start_days": next(
            item["missing_at_start"] for item in series
            if item["asset"] == "SOL" and item["metric"] == "ecosystem_daily_revenue_usd"
        ),
        "errors": [],
    }
