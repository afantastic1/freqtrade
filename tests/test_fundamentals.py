import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from data_engineering.catalog import publish
from data_engineering.fundamentals import FundamentalDataAdapter


FIELDS = ["asset_id", "metric", "event_time", "value", "published_at"]


def _source(tmp_path: Path, rows, *, name="fundamental.csv") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(dict.fromkeys(FIELDS + [key for row in rows for key in row]))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(day, *, asset="btc", metric="CapMrktEstUSD", published_at=""):
    return {"asset_id": asset, "metric": metric,
            "event_time": f"{day}T00:00:00Z", "value": "123.5",
            "published_at": published_at}


def _publish(tmp_path, *, dataset_id="coinmetrics_btc_dev_v1", rows=None,
             source="coinmetrics-community"):
    rows = rows or [_row("2024-01-01"), _row("2024-01-02")]
    source_path = _source(tmp_path, rows)
    manifest = publish(tmp_path / "catalog", dataset_id=dataset_id,
                       kind="fundamental", source=source,
                       input_path=source_path)
    return tmp_path / "catalog", manifest


def test_exploratory_read_is_normalized_and_keeps_unknown_availability_null(tmp_path):
    root, manifest = _publish(tmp_path)
    adapter = FundamentalDataAdapter(root)

    result = adapter.read(manifest["dataset_id"], manifest["sha256"],
                          access="exploratory")

    assert list(result.columns) == [
        "asset_id", "tradable_asset_id", "entity_scope", "metric",
        "event_time", "available_time", "value", "source", "dataset_id",
        "snapshot_sha256", "pit_status",
    ]
    assert result.asset_id.tolist() == ["token:BTC", "token:BTC"]
    assert result.tradable_asset_id.tolist() == ["BTC", "BTC"]
    assert result.available_time.isna().all()
    assert result.pit_status.tolist() == ["unverified", "unverified"]
    assert result.snapshot_sha256.eq(manifest["sha256"]).all()
    assert result.attrs["asset_registry_version"] == "asset-registry-v1"
    assert result.attrs["metric_registry_version"] == "metric-registry-v1"
    assert result.attrs["access_mode"] == "exploratory"
    assert len(adapter.list_snapshots()) == 1


def test_declared_publication_timestamp_remains_unverified(tmp_path):
    root, manifest = _publish(
        tmp_path,
        rows=[_row("2024-01-01", published_at="2024-01-02T03:00:00Z")],
    )

    result = FundamentalDataAdapter(root).read(
        manifest["dataset_id"], manifest["sha256"], access="exploratory"
    )

    assert result.available_time.iloc[0] == pd.Timestamp("2024-01-02T03:00:00Z")
    assert result.pit_status.iloc[0] == "declared_not_audited"
    with pytest.raises(ValueError, match="PIT-unverified"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="strict_pit"
        )


def test_published_at_fills_null_available_time_without_fabrication(tmp_path):
    row = _row("2024-01-01", published_at="2024-01-02T03:00:00Z")
    row["available_time"] = ""
    root, manifest = _publish(tmp_path, rows=[row])

    result = FundamentalDataAdapter(root).read(
        manifest["dataset_id"], manifest["sha256"], access="exploratory"
    )

    assert result.available_time.iloc[0] == pd.Timestamp("2024-01-02T03:00:00Z")
    assert result.pit_status.iloc[0] == "declared_not_audited"


def test_adapter_assigns_network_metrics_to_chain_entities(tmp_path):
    root, manifest = _publish(
        tmp_path,
        rows=[_row("2024-01-01", metric="TxCnt")],
    )

    result = FundamentalDataAdapter(root).read(
        manifest["dataset_id"], manifest["sha256"], access="exploratory"
    )

    assert result.asset_id.tolist() == ["chain:Bitcoin"]
    assert result.entity_scope.tolist() == ["chain"]
    assert result.tradable_asset_id.tolist() == ["BTC"]


def test_strict_pit_accepts_only_attested_actual_availability_time(tmp_path):
    row = _row("2024-01-01")
    row["available_time"] = "2024-01-02T03:00:00Z"
    source_path = _source(tmp_path, [row], name="with-availability.csv")
    root = tmp_path / "catalog"
    manifest = publish(root, dataset_id="coinmetrics_btc_dev_v1",
                       kind="fundamental", source="coinmetrics-community",
                       input_path=source_path)
    evidence = {"dataset_id": manifest["dataset_id"],
                "snapshot_sha256": manifest["sha256"],
                "status": "verified", "reviewer": "data-steward",
                "audit_reference": "audit-2024-001",
                "availability_column": "available_time_or_published_at"}
    evidence_bytes = (json.dumps(evidence, sort_keys=True) + "\n").encode()
    evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
    evidence_dir = root / "pit_attestations"
    evidence_dir.mkdir()
    (evidence_dir / f"{evidence_hash}.json").write_bytes(evidence_bytes)
    # A content-addressed sidecar is append-only; snapshot and manifest stay intact.
    assert evidence_bytes == (evidence_dir / f"{evidence_hash}.json").read_bytes()

    result = FundamentalDataAdapter(root).read(
        manifest["dataset_id"], manifest["sha256"], access="strict_pit"
    )

    assert result.pit_status.tolist() == ["verified"]
    assert result.available_time.iloc[0] == pd.Timestamp("2024-01-02T03:00:00Z")
    assert result.attrs["pit_attestation_sha256"] == evidence_hash
    assert result.attrs["_strict_pit_read_token"] is not None


def test_attestation_checksum_and_snapshot_binding_fail_closed(tmp_path):
    row = _row("2024-01-01")
    row["available_time"] = "2024-01-02T03:00:00Z"
    source_path = _source(tmp_path, [row], name="with-availability.csv")
    root = tmp_path / "catalog"
    manifest = publish(root, dataset_id="coinmetrics_btc_dev_v1", kind="fundamental",
                       source="coinmetrics-community", input_path=source_path)
    attestation = {"dataset_id": manifest["dataset_id"],
                   "snapshot_sha256": manifest["sha256"], "status": "verified",
                   "reviewer": "steward", "audit_reference": "audit-1",
                   "availability_column": "available_time_or_published_at"}
    payload = (json.dumps(attestation, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    directory = root / "pit_attestations"
    directory.mkdir()
    path = directory / f"{digest}.json"
    path.write_bytes(payload)
    adapter = FundamentalDataAdapter(root)
    # Valid sidecar passes; changing bytes without changing its address is rejected.
    assert adapter.read(manifest["dataset_id"], manifest["sha256"],
                        access="strict_pit").pit_status.iloc[0] == "verified"
    path.write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="PIT-unverified"):
        adapter.read(manifest["dataset_id"], manifest["sha256"], access="strict_pit")


def test_read_requires_version_pin_and_rejects_out_of_window_ranges(tmp_path):
    root, manifest = _publish(tmp_path)
    adapter = FundamentalDataAdapter(root)

    with pytest.raises(TypeError):
        adapter.read(manifest["dataset_id"], access="exploratory")
    with pytest.raises(ValueError, match="development window"):
        adapter.read(manifest["dataset_id"], manifest["sha256"],
                     access="exploratory",
                     start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                     end=datetime(2025, 1, 2, tzinfo=timezone.utc))


def test_read_many_rejects_multiple_versions_of_one_dataset(tmp_path):
    catalog = tmp_path / "catalog"
    first_source = _source(tmp_path / "first", [_row("2024-01-01")])
    second_source = _source(tmp_path / "second", [dict(_row("2024-01-01"), value="999")])
    first = publish(catalog, dataset_id="coinmetrics_btc_dev_v1", kind="fundamental",
                    source="coinmetrics-community", input_path=first_source)
    second = publish(catalog, dataset_id="coinmetrics_btc_dev_v1", kind="fundamental",
                     source="coinmetrics-community", input_path=second_source)

    with pytest.raises(ValueError, match="one version per dataset_id"):
        FundamentalDataAdapter(catalog).read_many(
            [("coinmetrics_btc_dev_v1", first["sha256"]),
             ("coinmetrics_btc_dev_v1", second["sha256"])],
            access="exploratory",
        )


def test_read_rejects_publication_times_outside_development_window(tmp_path):
    root, manifest = _publish(
        tmp_path,
        rows=[_row("2024-12-31", published_at="2025-01-02T00:00:00Z")],
    )

    with pytest.raises(ValueError, match="availability time outside development window"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


def test_read_rejects_available_time_before_event_time(tmp_path):
    row = _row("2024-01-02")
    row["available_time"] = "2024-01-01T23:59:00Z"
    root, manifest = _publish(tmp_path, rows=[row])

    with pytest.raises(ValueError, match="precedes event time"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


@pytest.mark.parametrize(
    "asset,metric,source,match",
    [
        ("doge", "CapMrktEstUSD", "coinmetrics-community", "unknown source asset"),
        ("btc", "ecosystem_daily_fees_usd", "coinmetrics-community", "not registered for source"),
    ],
)
def test_unknown_economic_identity_or_metric_is_rejected(tmp_path, asset, metric,
                                                          source, match):
    root, manifest = _publish(
        tmp_path, rows=[_row("2024-01-01", asset=asset, metric=metric)],
        source=source,
    )

    with pytest.raises(ValueError, match=match):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


def test_market_snapshot_cannot_be_read_as_fundamental(tmp_path):
    market = tmp_path / "market.csv"
    market.write_text(
        "open_time,symbol,open,high,low,close,volume,close_time,available_time\n"
        "2024-01-01T00:00:00Z,BTC/USDT:USDT,1,1,1,1,1,2024-01-01T01:00:00Z,2024-01-01T01:00:01Z\n"
    )
    root = tmp_path / "catalog"
    manifest = publish(root, dataset_id="market", kind="market",
                       source="bounded-market", input_path=market,
                       timeframe="1h")

    with pytest.raises(ValueError, match="not a fundamental snapshot"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


def test_manifest_or_snapshot_tampering_is_rejected(tmp_path):
    root, manifest = _publish(tmp_path)
    snapshot = root / "snapshots" / manifest["dataset_id"] / f"{manifest['sha256']}.csv"
    snapshot.write_text(snapshot.read_text() + "tampered")

    with pytest.raises(ValueError, match="integrity"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


def test_manifest_identity_tampering_is_rejected(tmp_path):
    root, manifest = _publish(tmp_path)
    path = root / "snapshots" / manifest["dataset_id"] / f"{manifest['sha256']}.manifest.json"
    document = json.loads(path.read_text())
    document["dataset_id"] = "another_dataset"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest identity mismatch"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )


def test_symlinked_snapshot_directory_is_rejected(tmp_path):
    external_root, manifest = _publish(tmp_path / "external")
    root = tmp_path / "research-catalog"
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / manifest["dataset_id"]).symlink_to(
        external_root / "snapshots" / manifest["dataset_id"], target_is_directory=True
    )

    with pytest.raises(ValueError, match="symlink"):
        FundamentalDataAdapter(root).read(
            manifest["dataset_id"], manifest["sha256"], access="exploratory"
        )
