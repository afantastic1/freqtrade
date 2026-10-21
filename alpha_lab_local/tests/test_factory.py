from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _config(root: Path) -> dict:
    return {
        "catalog_root": str(root / "catalog"),
        "source_roots": {"alpha-lab-normalized": str(root / "normalized")},
        "dataset_registry": {
            "binance:futures:BTC:ohlcv:5m": "binance-futures-btc-ohlcv-5m-6c7cb41c78f4"
        },
        "dataset_output_root": str(root / "state" / "datasets"),
        "research_request_path": str(root / "state" / "requests.sqlite3"),
        "research_store_path": str(root / "state" / "experiments.sqlite3"),
        "worker_profile_path": str(ROOT / "containers/alpha-lab-factor-worker/profile.json"),
        "broker_endpoint": "http://127.0.0.1:8765",
        "campaign_id": "dataset_builder_acceptance",
        "policy_version": "1",
        "campaign_policy_sha256": "b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f",
        "dataset_key": "binance:futures:BTC:ohlcv:5m",
    }


def test_factory_builds_trusted_v2_and_agent_dependencies_from_private_config(tmp_path, monkeypatch):
    from alpha_lab_local.factory import create

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config_path.chmod(0o600)
    monkeypatch.setenv("ALPHA_LAB_LOCAL_CONFIG", str(config_path))
    monkeypatch.setenv("ALPHA_LAB_BROKER_TOKEN", "trusted-broker-token-0123456789")

    services = create()

    assert services.agent_gateway is not None
    assert services.v2_service is not None
    assert services.v2_service.store.path == Path(_config(tmp_path)["research_store_path"])
    assert services.v2_service.worker_identity == (
        "sha256:bb2aced00809732898f7c62c4a525af71beebc1190d60ba78c0924ee5862119f"
    )
    assert services.agent_gateway.isolation_verifier is not None


def test_factory_rejects_policy_or_catalog_widening(tmp_path):
    from alpha_lab_local.config import RuntimeConfig

    config = _config(tmp_path)
    config["campaign_id"] = "example_campaign"
    with pytest.raises(ValueError, match="fixed BTC Discovery campaign"):
        RuntimeConfig.from_mapping(config)

    config = _config(tmp_path)
    config["dataset_registry"]["binance:futures:ETH:ohlcv:5m"] = "other-dataset"
    with pytest.raises(ValueError, match="fixed BTC 5m Catalog"):
        RuntimeConfig.from_mapping(config)


def test_live_isolation_verifier_fails_closed_outside_verified_service_container(tmp_path):
    from alpha_lab_local.config import RuntimeConfig
    from alpha_lab_local.isolation import LocalIsolationVerifierV2
    from alpha_lab_service.worker_profile import profile_from_file

    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    config = RuntimeConfig.from_mapping(_config(root))
    profile = profile_from_file(config.worker_profile_path)

    report = LocalIsolationVerifierV2(config, profile).verify(worker_image_digest=profile.image)

    assert report.worker_image_digest == profile.image
    assert set(report.checks) == {
        "worker_image_immutable", "worker_os_identity_separated", "network_disabled",
        "docker_socket_unavailable", "read_only_root_filesystem", "data_mount_read_only",
        "stores_write_restricted", "resource_limits_enforced",
    }
    assert not report.permits(worker_image_digest=profile.image)


def test_dataset_builder_rule_tracks_request_timeframe_without_a_5m_lock():
    from types import SimpleNamespace

    from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
    from alpha_lab_local.factory import TimeframeDatasetBuilder

    class CaptureBuilder:
        def build(self, request, *, options, universe=None):
            return options.availability_rule_ids

    wrapper = TimeframeDatasetBuilder(CaptureBuilder())
    options = DatasetBuildOptionsV1("reject", "futures_ohlcv", {}, {}, {})

    result = wrapper.build(SimpleNamespace(timeframe="4h"), options=options)

    assert result == {"futures_ohlcv": "binance-close-plus-30s-4h"}


def test_coverage_gateway_does_not_query_catalog_outside_discovery():
    from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
    from alpha_lab_local.config import CAMPAIGN_ID, POLICY_SHA256, POLICY_VERSION
    from alpha_lab_local.factory import DiscoveryCoverageGateway

    class CoverageProbe:
        called = False

        def get_data_coverage(self, **_kwargs):
            self.called = True
            return {"status": "AVAILABLE"}

    policy = FileCampaignPolicyRegistry().get(CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256)
    probe = CoverageProbe()
    gateway = DiscoveryCoverageGateway(probe, policy)

    result = gateway.get_data_coverage(
        dataset_key="binance:futures:BTC:ohlcv:5m",
        start="2026-01-01T00:00:00Z",
        end="2026-01-02T00:00:00Z",
    )

    assert result == {
        "status": "AUTHORIZATION_DENIED",
        "error_code": "OUTSIDE_DISCOVERY_CAMPAIGN",
    }
    assert not probe.called


def test_host_preflight_accepts_only_the_mounted_discovery_years(tmp_path):
    from alpha_lab_local.config import DATASET_ID
    from alpha_lab_local.host_checks import _discovery_partitions_only

    dataset = tmp_path / DATASET_ID
    dataset.mkdir()
    for year in (2022, 2023, 2024):
        (dataset / f"year={year}").mkdir()
    assert _discovery_partitions_only(tmp_path)

    (dataset / "year=2026").mkdir()
    assert not _discovery_partitions_only(tmp_path)


def test_factory_import_does_not_require_freqtrade_runtime_package():
    blocker = """
import importlib.abc
import sys
class BlockFreqtrade(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname == 'freqtrade' or fullname.startswith('freqtrade.')
                or fullname == 'cachetools' or fullname.startswith('cachetools.')):
            raise ModuleNotFoundError(f'{fullname} intentionally unavailable in service image')
sys.meta_path.insert(0, BlockFreqtrade())
import alpha_lab_local.factory
"""
    result = subprocess.run(
        [sys.executable, "-c", blocker], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_built_service_image_starts_and_imports_factory_without_freqtrade():
    image = os.environ.get("ALPHA_LAB_LOCAL_SERVICE_IMAGE")
    if not image:
        pytest.skip("Docker service image smoke test is enabled by CI and WSL acceptance")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
         "--entrypoint", "python", image, "-c",
         "import alpha_lab_local.factory"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
