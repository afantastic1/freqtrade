import hashlib
import json

import pandas as pd
import pytest

from data_engineering.catalog import publish
from data_engineering.pit import validate_pit_sidecar


def _published(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "asset_id,metric,event_time,value,published_at\n"
        "btc,CapMrktEstUSD,2024-01-01T00:00:00Z,1,\n",
        encoding="utf-8",
    )
    manifest = publish(
        tmp_path / "catalog", dataset_id="coinmetrics_btc_dev_v1",
        kind="fundamental", source="coinmetrics-community", input_path=source,
    )
    row = {"asset_id": "btc", "metric": "CapMrktEstUSD",
           "event_time": "2024-01-01T00:00:00Z", "value": "1"}
    return tmp_path / "catalog", manifest, [row]


def _sidecar(manifest, *, status="VERIFIED", available_at="2024-01-02T00:00:00Z",
             upper_bound=None, basis="source_release_record", evidence_hashes=None,
             revision_id="cm-v42", event_time="2024-01-01T00:00:00Z"):
    record = {"source_asset_id": "btc", "metric": "CapMrktEstUSD",
              "event_time": event_time, "revision_id": revision_id,
              "status": status, "availability_basis": basis,
              "evidence_sha256s": evidence_hashes or []}
    if status in {"UNVERIFIED", "REJECTED"}:
        record["reason"] = "historical publication and revision evidence is incomplete"
    if available_at is not None:
        record["available_at"] = available_at
    if upper_bound is not None:
        record["available_at_upper_bound"] = upper_bound
    return {
        "schema_version": "pit-evidence-v1",
        "dataset_id": manifest["dataset_id"], "snapshot_sha256": manifest["sha256"],
        "source": manifest["source"], "ingested_at": manifest["ingested_at"],
        "status": status,
        **({"reason": "historical publication and revision evidence is incomplete"}
           if status in {"UNVERIFIED", "REJECTED"} else {}),
        "review": {"reviewer": "steward", "audit_reference": "audit-2024-1",
                   "reviewed_at": "2026-10-19T00:00:00Z"},
        "evidence": [
            {"sha256": digest, "source_uri": f"https://evidence.example/{digest}",
             "media_type": "application/json"}
            for digest in evidence_hashes or []
        ],
        "records": [record],
    }


def _encode(document):
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return payload, hashlib.sha256(payload).hexdigest()


def _write_evidence(root, payload):
    digest = hashlib.sha256(payload).hexdigest()
    directory = root / "pit_evidence"
    directory.mkdir(exist_ok=True)
    (directory / digest).write_bytes(payload)
    return digest


def test_verified_requires_row_level_release_evidence_and_revision(tmp_path):
    root, manifest, rows = _published(tmp_path)
    evidence = json.dumps({"kind": "source_release_record", "revision_id": "cm-v42",
                           "available_at": "2024-01-02T00:00:00Z"}).encode()
    evidence_hash = _write_evidence(root, evidence)
    payload, digest = _encode(_sidecar(manifest, evidence_hashes=[evidence_hash]))

    result = validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                                  sidecar_sha256=digest)

    record = result.records[0]
    assert result.status == "VERIFIED"
    assert record.available_at == pd.Timestamp("2024-01-02T00:00:00Z")
    assert record.revision_id == "cm-v42"
    assert record.ingested_at == pd.Timestamp(manifest["ingested_at"])


def test_conservative_requires_audited_upper_bound_evidence(tmp_path):
    root, manifest, rows = _published(tmp_path)
    evidence_hash = _write_evidence(root, json.dumps({
        "kind": "audited_upper_bound", "revision_id": "cm-v42",
        "available_at_upper_bound": "2024-01-05T00:00:00Z",
    }).encode())
    sidecar = _sidecar(manifest, status="CONSERVATIVE", available_at=None,
                       upper_bound="2024-01-05T00:00:00Z", basis="audited_upper_bound",
                       evidence_hashes=[evidence_hash])
    payload, digest = _encode(sidecar)

    result = validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                                  sidecar_sha256=digest)

    assert result.status == "CONSERVATIVE"
    assert result.records[0].effective_available_at == pd.Timestamp("2024-01-05T00:00:00Z")


def test_unverified_cannot_claim_event_time_or_fixed_delay_as_availability(tmp_path):
    root, manifest, rows = _published(tmp_path)
    sidecar = _sidecar(
        manifest, status="UNVERIFIED", available_at="2024-01-01T00:00:00Z",
        basis="unknown", evidence_hashes=[], revision_id=None,
    )
    payload, digest = _encode(sidecar)

    with pytest.raises(ValueError, match="UNVERIFIED records cannot claim availability"):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256=digest)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"event_time": "2024-01-03T00:00:00Z"}, "event_time mismatch"),
        ({"available_at": "2023-12-31T23:59:59Z"}, "precedes event_time"),
        ({"revision_id": None}, "revision_id is required"),
        ({"evidence_sha256s": []}, "require revision_id and evidence"),
    ],
)
def test_verified_rejects_conflicting_or_incomplete_row_evidence(tmp_path, changes, match):
    root, manifest, rows = _published(tmp_path)
    evidence_hash = _write_evidence(root, json.dumps({
        "kind": "source_release_record", "revision_id": "cm-v42",
        "available_at": "2024-01-02T00:00:00Z",
    }).encode())
    record = _sidecar(manifest, evidence_hashes=[evidence_hash])["records"][0]
    record.update(changes)
    payload, digest = _encode({**_sidecar(manifest, evidence_hashes=[evidence_hash]),
                               "records": [record]})

    with pytest.raises(ValueError, match=match):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256=digest)


def test_sidecar_and_evidence_hash_mismatch_fail_closed(tmp_path):
    root, manifest, rows = _published(tmp_path)
    evidence_hash = _write_evidence(root, json.dumps({
        "kind": "source_release_record", "revision_id": "cm-v42",
        "available_at": "2024-01-02T00:00:00Z",
    }).encode())
    payload, digest = _encode(_sidecar(manifest, evidence_hashes=[evidence_hash]))
    with pytest.raises(ValueError, match="sidecar SHA-256 mismatch"):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256="0" * 64)

    path = root / "pit_evidence" / evidence_hash
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="evidence SHA-256 mismatch"):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256=digest)


def test_sidecar_cannot_cover_a_different_snapshot_or_future_ingestion(tmp_path):
    root, manifest, rows = _published(tmp_path)
    sidecar = _sidecar(manifest, status="UNVERIFIED", available_at=None,
                       basis="unknown", revision_id=None)
    sidecar["snapshot_sha256"] = "f" * 64
    payload, digest = _encode(sidecar)
    with pytest.raises(ValueError, match="snapshot identity mismatch"):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256=digest)

    sidecar = _sidecar(manifest, status="UNVERIFIED", available_at=None,
                       basis="unknown", revision_id=None)
    sidecar["ingested_at"] = "2027-01-01T00:00:00Z"
    payload, digest = _encode(sidecar)
    with pytest.raises(ValueError, match="ingested_at mismatch"):
        validate_pit_sidecar(root, manifest, rows, sidecar_bytes=payload,
                             sidecar_sha256=digest)
