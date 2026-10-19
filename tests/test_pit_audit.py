import csv
import hashlib
import json

from data_engineering.catalog import publish
from data_engineering.pit_audit import build_pit_audit, render_markdown


def test_pit_audit_reports_all_snapshot_metrics_unverified(tmp_path):
    snapshots = [
        ("cm", "coinmetrics-community", "btc", "CapMrktEstUSD"),
        ("llama", "defillama-chain-tvl", "chain:Solana", "chain_tvl_usd"),
    ]
    for dataset, source, asset, metric in snapshots:
        csv_path = tmp_path / f"{dataset}.csv"
        with csv_path.open("w", newline="", encoding="utf8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["asset_id", "metric", "event_time", "value"])
            writer.writerow([asset, metric, "2024-01-01T00:00:00Z", 1])
        publish(tmp_path / "catalog", dataset_id=dataset, kind="fundamental",
                source=source, input_path=csv_path)

    report = build_pit_audit(tmp_path / "catalog")

    assert report["dataset_count"] == 2
    assert report["metric_series_count"] == 2
    assert report["strict_eligible_dataset_count"] == 0
    assert all(item["pit_status"] == "UNVERIFIED" for item in report["datasets"])
    assert all(not item["strict_research_eligible"] for item in report["datasets"])
    assert "historical source vintages" in render_markdown(report)


def test_pit_audit_requires_a_complete_valid_sidecar_for_strict_eligibility(tmp_path):
    catalog = tmp_path / "catalog"
    source = tmp_path / "verified.csv"
    with source.open("w", newline="", encoding="utf8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["asset_id", "metric", "event_time", "value"])
        writer.writerow(["btc", "CapMrktEstUSD", "2024-01-01T00:00:00Z", 1])
    manifest = publish(catalog, dataset_id="verified", kind="fundamental",
                       source="coinmetrics-community", input_path=source)
    evidence = json.dumps({"kind": "source_release_record", "revision_id": "r1",
                           "available_at": "2024-01-02T00:00:00Z"}).encode()
    evidence_hash = hashlib.sha256(evidence).hexdigest()
    (catalog / "pit_evidence").mkdir()
    (catalog / "pit_evidence" / evidence_hash).write_bytes(evidence)
    sidecar = {
        "schema_version": "pit-evidence-v1", "dataset_id": "verified",
        "snapshot_sha256": manifest["sha256"], "source": manifest["source"],
        "ingested_at": manifest["ingested_at"], "status": "VERIFIED",
        "review": {"reviewer": "audit", "audit_reference": "proof-1",
                   "reviewed_at": "2026-10-19T00:00:00Z"},
        "evidence": [{"sha256": evidence_hash, "source_uri": "https://source/release",
                      "media_type": "application/json"}],
        "records": [{"source_asset_id": "btc", "metric": "CapMrktEstUSD",
                     "event_time": "2024-01-01T00:00:00Z", "revision_id": "r1",
                     "status": "VERIFIED", "availability_basis": "source_release_record",
                     "available_at": "2024-01-02T00:00:00Z",
                     "evidence_sha256s": [evidence_hash]}],
    }
    encoded = (json.dumps(sidecar, sort_keys=True) + "\n").encode()
    sidecar_hash = hashlib.sha256(encoded).hexdigest()
    sidecar_dir = catalog / "pit_attestations"
    sidecar_dir.mkdir()
    (sidecar_dir / f"{sidecar_hash}.json").write_bytes(encoded)

    report = build_pit_audit(catalog)

    assert report["strict_eligible_dataset_count"] == 1
    assert report["snapshots_opened_for_sidecar_validation"] == 1
    assert report["datasets"][0]["pit_status"] == "VERIFIED"
