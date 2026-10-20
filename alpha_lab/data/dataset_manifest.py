"""Content-addressed immutable publication for built dataset products."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from jsonschema import Draft202012Validator, FormatChecker
from importlib.resources import files

from .build_products import DatasetArtifactV1, DatasetBuildResultV2


def _canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def dataset_manifest_fingerprint(document: dict) -> str:
    canonical = dict(document)
    canonical.pop("created_at", None)
    return hashlib.sha256(_canonical(canonical)).hexdigest()


def _safe_path(path: Path) -> Path:
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("symlinked output paths are forbidden")
    return path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"conflicting bytes at immutable artifact {path.name}")
        return
    fd, temp_name = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"conflicting bytes at immutable artifact {path.name}")
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _parquet_bytes(frame) -> bytes:
    import io

    stream = io.BytesIO()
    frame.to_parquet(stream, engine="pyarrow", index=True, compression="zstd", version="2.6")
    return stream.getvalue()


def publish_dataset(result: DatasetBuildResultV2, output_root: Path) -> DatasetArtifactV1:
    root = _safe_path(Path(output_root))
    root.mkdir(parents=True, exist_ok=True)
    worker = _parquet_bytes(result.worker_input.frame)
    labels = _parquet_bytes(result.evaluator_labels.frame)
    report_doc = {"sources": list(result.report.sources), "issues": list(result.report.issues), "quality": dict(result.report.quality)}
    report = _canonical(report_doc)
    product_hashes = {"worker": _sha(worker), "labels": _sha(labels), "report": _sha(report)}
    document = dict(result.manifest)
    document["products"] = product_hashes
    document["created_at"] = document.get("created_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    fingerprint = dataset_manifest_fingerprint(document)
    document["build_fingerprint"] = fingerprint
    schema = json.loads(files("alpha_lab.data").joinpath("schemas", "alpha-lab-dataset-manifest-v1.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document), key=lambda error: list(error.path))
    if errors:
        raise ValueError(f"dataset manifest invalid: {errors[0].message}")
    manifest_bytes = _canonical(document)
    build_dir = root / fingerprint
    build_dir.mkdir(parents=True, exist_ok=True)
    _safe_path(build_dir)
    paths = {
        "worker": build_dir / f"worker-{product_hashes['worker']}.parquet",
        "labels": build_dir / f"labels-{product_hashes['labels']}.parquet",
        "report": build_dir / f"report-{product_hashes['report']}.json",
        "manifest": build_dir / f"manifest-{fingerprint}.json",
    }
    for name, data in (("worker", worker), ("labels", labels), ("report", report), ("manifest", manifest_bytes)):
        path = paths[name]
        if name == "manifest" and path.exists():
            existing = json.loads(path.read_text())
            existing.pop("build_fingerprint", None)
            if dataset_manifest_fingerprint(existing) != fingerprint:
                raise ValueError("conflicting manifest bytes")
        else:
            _atomic_create(path, data)
    return DatasetArtifactV1(
        worker_path=paths["worker"], labels_path=paths["labels"], report_path=paths["report"],
        manifest_path=paths["manifest"], fingerprints={**product_hashes, "manifest": fingerprint},
    )
