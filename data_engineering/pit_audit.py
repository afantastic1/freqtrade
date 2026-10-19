"""Metadata-only PIT eligibility audit for catalogued fundamental snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import csv

from .catalog import validate_manifest, verify
from .pit import validate_pit_sidecar


SOURCE_GAPS = {
    "coinmetrics-community": [
        "source publication timestamps or archived release records per observation",
        "historical source vintages proving the value was available at that time",
        "source revision identifiers bound to each observation",
    ],
    "defillama-chain-tvl": [
        "historical publication timestamps or point-in-time API vintages",
        "revision identifiers bound to each historical TVL value",
        "evidence proving a conservative availability upper bound",
    ],
    "defillama-chain-fees-overview": [
        "historical publication timestamps or point-in-time API vintages",
        "revision identifiers bound to each historical fee/revenue value",
        "evidence proving a conservative availability upper bound",
    ],
}


def build_pit_audit(root: str | Path) -> dict:
    """Validate manifests and any bound sidecars for physically bounded snapshots."""
    root = Path(root)
    snapshots = root / "snapshots"
    datasets = []
    errors = []
    snapshots_opened_for_sidecar_validation = 0
    for manifest_path in sorted(snapshots.glob("*/*.manifest.json")):
        dataset_id = manifest_path.parent.name
        digest = manifest_path.name.removesuffix(".manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_manifest(manifest)
            if manifest.get("dataset_id") != dataset_id or manifest.get("sha256") != digest:
                raise ValueError("manifest path and declared identity disagree")
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"dataset_id": dataset_id, "error": str(exc)})
            continue
        if manifest.get("kind") != "fundamental":
            continue
        quality = manifest.get("quality", {})
        metrics = quality.get("metrics", [])
        # Old snapshots expose metrics under their validation result; do not infer
        # any PIT status from these fields or from event dates.
        dataset = {
            "dataset_id": dataset_id,
            "snapshot_sha256": digest,
            "source": manifest.get("source"),
            "schema_version": manifest.get("schema_version"),
            "row_count": quality.get("rows"),
            "metrics": metrics,
            "ingested_at": manifest.get("ingested_at", manifest.get("published_at")),
            "pit_status": "UNVERIFIED",
            "strict_research_eligible": False,
            "missing_evidence": SOURCE_GAPS.get(
                manifest.get("source"), ["source-specific PIT evidence has not been audited"]
            ),
            "pit_sidecar_present": False,
        }
        attestation_dir = root / "pit_attestations"
        matches = []
        if attestation_dir.is_dir():
            for sidecar_path in sorted(attestation_dir.glob("*.json")):
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (sidecar.get("dataset_id") == dataset_id
                        and sidecar.get("snapshot_sha256") == digest):
                    matches.append((sidecar_path, sidecar))
        if len(matches) > 1:
            dataset["pit_status"] = "REJECTED"
            errors.append({"dataset_id": dataset_id,
                           "error": "multiple PIT sidecars bind the same snapshot"})
        elif matches:
            sidecar_path, _ = matches[0]
            dataset["pit_sidecar_present"] = True
            try:
                verified_manifest = verify(root, dataset_id=dataset_id, digest=digest)
                csv_path = root / "snapshots" / dataset_id / f"{digest}.csv"
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                snapshots_opened_for_sidecar_validation += 1
                assessment = validate_pit_sidecar(
                    root, verified_manifest, rows,
                    sidecar_bytes=sidecar_path.read_bytes(),
                    sidecar_sha256=sidecar_path.stem,
                )
                statuses = {record.status for record in assessment.records}
                if statuses <= {"VERIFIED", "CONSERVATIVE"}:
                    dataset["strict_research_eligible"] = True
                    dataset["pit_status"] = (
                        next(iter(statuses)) if len(statuses) == 1 else "MIXED"
                    )
                    dataset["missing_evidence"] = []
                else:
                    dataset["pit_status"] = (
                        next(iter(statuses)) if len(statuses) == 1 else "MIXED"
                    )
            except (OSError, ValueError, KeyError) as exc:
                dataset["pit_status"] = "REJECTED"
                errors.append({"dataset_id": dataset_id, "error": str(exc)})
        # Catalog v0.1 quality records metric names; v1 manifests may lack metrics.
        datasets.append(dataset)
    return {
        "audit_version": "pit-eligibility-audit-v1",
        "development_window": {"start_inclusive": "2022-01-01", "end_exclusive": "2025-01-01"},
        "snapshots_opened_for_sidecar_validation": snapshots_opened_for_sidecar_validation,
        "datasets": datasets,
        "dataset_count": len(datasets),
        "metric_series_count": sum(len(item["metrics"]) for item in datasets),
        "strict_eligible_dataset_count": sum(
            item["strict_research_eligible"] for item in datasets
        ),
        "errors": errors,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Fundamental PIT Eligibility Audit",
        "",
        f"- Audit version: `{report['audit_version']}`",
        f"- Window: `{report['development_window']['start_inclusive']}` to "
        f"`{report['development_window']['end_exclusive']}` (exclusive)",
        f"- Datasets: {report['dataset_count']}; metric series: {report['metric_series_count']}",
        f"- Strict eligible datasets: {report['strict_eligible_dataset_count']}",
        "- Strict eligibility is granted only after every sidecar row and evidence blob validates.",
        "",
        "| Dataset | Source | Metrics | PIT | Strict eligible | Missing evidence |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["datasets"]:
        metrics = ", ".join(item["metrics"]) or "not recorded in manifest"
        gaps = "; ".join(item["missing_evidence"])
        lines.append(
            f"| `{item['dataset_id']}` | `{item['source']}` | {metrics} | "
            f"{item['pit_status']} | no | {gaps} |"
        )
    if report["errors"]:
        lines.extend(["", "## Catalog errors", ""])
        lines.extend(f"- `{item['dataset_id']}`: {item['error']}" for item in report["errors"])
    lines.append("")
    return "\n".join(lines)


def write_audit(root: str | Path, json_path: str | Path, markdown_path: str | Path) -> dict:
    """Write reports outside the catalog; callers choose their destination."""
    report = build_pit_audit(root)
    Path(json_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(markdown_path).write_text(render_markdown(report), encoding="utf-8")
    return report
