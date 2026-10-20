import hashlib
import json

import pandas as pd
import pytest

from alpha_lab.data.build_products import DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2
from alpha_lab.data.dataset_manifest import dataset_manifest_fingerprint, publish_dataset


def result():
    worker = WorkerInputV2(pd.DataFrame({"available_time": pd.to_datetime(["2025-01-01T00:00Z"], utc=True), "close": [1.]}), ("available_time",))
    labels = EvaluatorLabelsV1(pd.DataFrame({"label_value": [0.1]}))
    manifest = {"schema_version": "alpha-lab-dataset-manifest-v1", "request_sha256": "a" * 64, "policy_sha256": "b" * 64, "source_pins": {"dataset": "c" * 64}}
    return DatasetBuildResultV2(worker, labels, DatasetBuildReportV1(), manifest)


def test_manifest_binds_request_policy_and_source_pins():
    fp = dataset_manifest_fingerprint(result().manifest)
    changed = dict(result().manifest, request_sha256="d" * 64)
    assert dataset_manifest_fingerprint(changed) != fp


def test_manifest_hash_excludes_only_created_at():
    doc = result().manifest
    assert dataset_manifest_fingerprint(dict(doc, created_at="2025-01-01T00:00:00Z")) == dataset_manifest_fingerprint(dict(doc, created_at="2025-01-02T00:00:00Z"))
    assert dataset_manifest_fingerprint(dict(doc, request_sha256="d" * 64, created_at="2025-01-01T00:00:00Z")) != dataset_manifest_fingerprint(dict(doc, created_at="2025-01-01T00:00:00Z"))


def test_repeated_publish_returns_same_fingerprints(tmp_path):
    first = publish_dataset(result(), tmp_path)
    second = publish_dataset(result(), tmp_path)
    assert first.fingerprints == second.fingerprints


def test_publish_never_overwrites_conflicting_bytes(tmp_path):
    artifact = publish_dataset(result(), tmp_path)
    artifact.worker_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="conflicting"):
        publish_dataset(result(), tmp_path)


def test_publish_rejects_symlinked_output_paths(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        publish_dataset(result(), alias)
