"""Strict, non-secret runtime configuration for the local Alpha Lab service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping


CAMPAIGN_ID = "dataset_builder_acceptance"
POLICY_VERSION = "2"
POLICY_SHA256 = "e42f7d34d03fe80c03fd09a771b97c90a5479cecb3e87463d5f1c5b6851404f2"
DATASET_ID = "binance-futures-btc-ohlcv-5m-6c7cb41c78f4"
DATASET_KEY = "binance:futures:BTC:ohlcv:5m"
SOURCE_ROOT_ID = "alpha-lab-normalized"
_CONFIG_KEYS = {
    "catalog_root", "source_roots", "dataset_registry", "dataset_output_root",
    "research_request_path", "research_store_path", "worker_profile_path",
    "broker_endpoint", "campaign_id", "policy_version", "campaign_policy_sha256",
    "dataset_key",
}


@dataclass(frozen=True)
class RuntimeConfig:
    catalog_root: Path
    source_roots: Mapping[str, Path]
    dataset_registry: Mapping[str, str]
    dataset_output_root: Path
    research_request_path: Path
    research_store_path: Path
    worker_profile_path: Path
    broker_endpoint: str
    campaign_id: str
    policy_version: str
    campaign_policy_sha256: str
    dataset_key: str

    @classmethod
    def from_mapping(cls, value: object) -> "RuntimeConfig":
        if not isinstance(value, dict) or set(value) != _CONFIG_KEYS:
            raise ValueError("local runtime config must contain exactly the trusted Factory fields")
        campaign_id = value["campaign_id"]
        policy_version = value["policy_version"]
        policy_sha256 = value["campaign_policy_sha256"]
        if (campaign_id, policy_version, policy_sha256) != (CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256):
            raise ValueError("only the pinned local data-expansion campaign is supported")
        roots = value["source_roots"]
        registry = value["dataset_registry"]
        if (not isinstance(roots, dict) or not roots
                or not isinstance(registry, dict) or not registry):
            raise ValueError("Catalog source roots and dataset registry must be nonempty mappings")
        if not isinstance(value["dataset_key"], str) or value["dataset_key"] not in registry:
            raise ValueError("dataset_key must name a registered Catalog dataset")
        if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", key)
               for key in roots):
            raise ValueError("Catalog source root IDs are invalid")
        if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,159}", key)
               or not isinstance(dataset_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,159}", dataset_id)
               for key, dataset_id in registry.items()):
            raise ValueError("Catalog dataset registry entries are invalid")
        paths: dict[str, Path] = {}
        for key in ("catalog_root", "dataset_output_root", "research_request_path",
                    "research_store_path", "worker_profile_path"):
            raw = value[key]
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ValueError(f"{key} must be an absolute operator path")
            paths[key] = Path(raw)
        source_roots = {}
        for root_id, raw_root in roots.items():
            if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
                raise ValueError("Catalog source roots must be absolute operator paths")
            source_roots[root_id] = Path(raw_root)
        endpoint = value["broker_endpoint"]
        if (not isinstance(endpoint, str)
                or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", endpoint)
                or not 1 <= int(endpoint.rsplit(":", 1)[1]) <= 65535):
            raise ValueError("trusted Broker endpoint must use the local loopback")
        store_parent = paths["research_store_path"].parent
        if paths["research_request_path"].parent != store_parent:
            raise ValueError("Research Store and Request Registry must share the private state directory")
        if paths["dataset_output_root"].parent != store_parent:
            raise ValueError("DatasetBuilder output must share the private state directory")
        return cls(
            catalog_root=paths["catalog_root"],
            source_roots=source_roots,
            dataset_registry=dict(registry),
            dataset_output_root=paths["dataset_output_root"],
            research_request_path=paths["research_request_path"],
            research_store_path=paths["research_store_path"],
            worker_profile_path=paths["worker_profile_path"],
            broker_endpoint=endpoint,
            campaign_id=campaign_id,
            policy_version=policy_version,
            campaign_policy_sha256=policy_sha256,
            dataset_key=value["dataset_key"],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "RuntimeConfig":
        import json

        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("trusted local runtime config cannot be read") from exc
        return cls.from_mapping(value)
