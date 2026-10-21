"""Strict, non-secret runtime configuration for the local Alpha Lab service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping


CAMPAIGN_ID = "dataset_builder_acceptance"
POLICY_VERSION = "1"
POLICY_SHA256 = "b0b2ca3f678c991a1d104f9141ea4f8434bebd987638a5754878ed7c838aa61f"
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
        if (value["campaign_id"], value["policy_version"], value["campaign_policy_sha256"]) != (
            CAMPAIGN_ID, POLICY_VERSION, POLICY_SHA256
        ):
            raise ValueError("only the fixed BTC Discovery campaign is supported")
        if value["dataset_key"] != DATASET_KEY:
            raise ValueError("only the fixed BTC 5m Catalog dataset is supported")
        roots = value["source_roots"]
        registry = value["dataset_registry"]
        if not isinstance(roots, dict) or set(roots) != {SOURCE_ROOT_ID}:
            raise ValueError("only the fixed BTC Catalog source root is supported")
        if not isinstance(registry, dict) or registry != {DATASET_KEY: DATASET_ID}:
            raise ValueError("only the fixed BTC 5m Catalog dataset is supported")
        paths: dict[str, Path] = {}
        for key in ("catalog_root", "dataset_output_root", "research_request_path",
                    "research_store_path", "worker_profile_path"):
            raw = value[key]
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ValueError(f"{key} must be an absolute operator path")
            paths[key] = Path(raw)
        raw_roots = roots[SOURCE_ROOT_ID]
        if not isinstance(raw_roots, str) or not Path(raw_roots).is_absolute():
            raise ValueError("Catalog source root must be an absolute operator path")
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
            source_roots={SOURCE_ROOT_ID: Path(raw_roots)},
            dataset_registry=dict(registry),
            dataset_output_root=paths["dataset_output_root"],
            research_request_path=paths["research_request_path"],
            research_store_path=paths["research_store_path"],
            worker_profile_path=paths["worker_profile_path"],
            broker_endpoint=endpoint,
            campaign_id=CAMPAIGN_ID,
            policy_version=POLICY_VERSION,
            campaign_policy_sha256=POLICY_SHA256,
            dataset_key=DATASET_KEY,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "RuntimeConfig":
        import json

        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("trusted local runtime config cannot be read") from exc
        return cls.from_mapping(value)
