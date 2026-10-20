"""Versioned declarative feature inputs for the trusted CS runner."""

from __future__ import annotations

import hashlib
import json
import re

import pandas as pd

from alpha_lab.data.market_panel import MarketPanel
from data_engineering.catalog import START
from data_engineering.alpha_lab_features import build_fundamental_features
from data_engineering.fundamentals import FundamentalDataAdapter, _validated_max_age
from data_engineering.registries import AssetRegistry, MetricRegistry


FEATURE_SCHEMA_VERSION = "alpha-cs-feature-schema-v1"
LEGACY_MARKET_SCHEMA_VERSION = "alpha-cs-market-features-v1"
MISSINGNESS_POLICY = "complete_case_dropna_no_fill-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_fundamental_spec(spec: dict) -> dict:
    if set(spec) != {"kind", "metric", "max_age", "snapshot_pins"} or spec["kind"] != "fundamental":
        raise ValueError("unsupported feature specification")
    metric = spec["metric"]
    if not isinstance(metric, str) or not metric:
        raise ValueError("fundamental metric is required")
    MetricRegistry().resolve_metric(metric)
    _validated_max_age(spec["max_age"])
    pins = spec["snapshot_pins"]
    if not isinstance(pins, list) or not pins:
        raise ValueError("fundamental features require explicit snapshot pins")
    normalized_pins = []
    for pin in pins:
        if (not isinstance(pin, dict) or set(pin) != {"dataset_id", "sha256"}
                or not isinstance(pin["dataset_id"], str) or not pin["dataset_id"]
                or not isinstance(pin["sha256"], str) or not SHA256.fullmatch(pin["sha256"])):
            raise ValueError("invalid fundamental snapshot pin")
        normalized_pins.append(dict(pin))
    if len({pin["dataset_id"] for pin in normalized_pins}) != len(normalized_pins):
        raise ValueError("pin at most one snapshot per fundamental dataset")
    return {**spec, "snapshot_pins": sorted(
        normalized_pins, key=lambda pin: pin["dataset_id"]
    )}


def validate_feature_specs(features: list, feature_schema_version: str | None) -> list[dict]:
    if not isinstance(features, list) or not features:
        raise ValueError("at least one feature is required")
    normalized = []
    uses_versioned_schema = False
    for spec in features:
        if not isinstance(spec, dict):
            raise ValueError("feature specification must be an object")
        if set(spec) == {"name", "periods"}:
            if not isinstance(spec["name"], str) or type(spec["periods"]) is not int:
                raise ValueError("invalid legacy market feature specification")
            normalized.append({"kind": "market", **spec})
            continue
        uses_versioned_schema = True
        if set(spec) == {"kind", "name", "periods"} and spec["kind"] == "market":
            if not isinstance(spec["name"], str) or type(spec["periods"]) is not int:
                raise ValueError("invalid market feature specification")
            normalized.append(dict(spec))
            continue
        normalized.append(_validate_fundamental_spec(spec))
    if uses_versioned_schema and feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ValueError("feature_schema_version must match the declared feature contract")
    if feature_schema_version not in (None, FEATURE_SCHEMA_VERSION):
        raise ValueError("unsupported feature_schema_version")
    if len({json.dumps(spec, sort_keys=True) for spec in normalized}) != len(normalized):
        raise ValueError("duplicate feature specification")
    return normalized


def _read_strict_observations(
    specs: list[dict], *, start, end,
    adapter: FundamentalDataAdapter | None,
) -> pd.DataFrame | None:
    fundamental_specs = [spec for spec in specs if spec["kind"] == "fundamental"]
    if not fundamental_specs:
        return None
    pin_map = {
        (pin["dataset_id"], pin["sha256"])
        for spec in fundamental_specs for pin in spec["snapshot_pins"]
    }
    if len({dataset_id for dataset_id, _ in pin_map}) != len(pin_map):
        raise ValueError("conflicting fundamental snapshot pins")
    start_time = pd.Timestamp(start)
    if start_time.tzinfo is None:
        raise ValueError("feature input start must include a timezone")
    start_time = start_time.tz_convert("UTC")
    warmup = max(_validated_max_age(spec["max_age"]) for spec in fundamental_specs)
    read_start = max(start_time - warmup, pd.Timestamp(START))
    return (adapter or FundamentalDataAdapter()).read_many(
        sorted(pin_map), access="strict_pit", start=read_start, end=end
    )


def _materialize_feature_series(
    specs: list[dict], panel: MarketPanel, *, timeframe: str,
    transforms: dict, observations: pd.DataFrame | None,
) -> dict[str, pd.Series]:
    output = {}
    metric_registry = MetricRegistry()
    for spec in specs:
        if spec["kind"] == "market":
            name = f"{spec['name']}_{spec['periods']}"
            transform = transforms.get(spec["name"])
            if transform is None or spec["periods"] < 1:
                raise ValueError("unsupported market feature or periods")
            values = transform(panel, periods=spec["periods"], timeframe=timeframe)
        else:
            metric = spec["metric"]
            definition = metric_registry.resolve_metric(metric)
            name = f"fundamental__{definition.entity_scope}__{metric}"
            values = build_fundamental_features(
                panel, observations, metrics=[metric], max_age=spec["max_age"]
            )[name]
        if name in output:
            raise ValueError("duplicate feature identity")
        output[name] = values
    return output


def _feature_provenance(
    output: dict[str, pd.Series], observations: pd.DataFrame | None,
    feature_schema_version: str | None,
) -> dict:
    asset_registry = AssetRegistry()
    metric_registry = MetricRegistry()
    feature_hashes = {
        name: hashlib.sha256(
            pd.util.hash_pandas_object(series, index=True).to_numpy().tobytes()
        ).hexdigest()
        for name, series in output.items()
    }
    return {
        "feature_schema_version": feature_schema_version or LEGACY_MARKET_SCHEMA_VERSION,
        "snapshot_pins": sorted(
            [list(pin) for pin in (observations.attrs.get("snapshot_pins", [])
                                   if observations is not None else [])]
        ),
        "pit_attestation_sha256": sorted(
            observations.attrs.get("pit_attestation_sha256", [])
            if observations is not None else []
        ),
        "asset_registry_version": (
            observations.attrs.get("asset_registry_version", asset_registry.version)
            if observations is not None else asset_registry.version
        ),
        "metric_registry_version": (
            observations.attrs.get("metric_registry_version", metric_registry.version)
            if observations is not None else metric_registry.version
        ),
        "missingness_policy": MISSINGNESS_POLICY,
        "feature_data_fingerprint": hashlib.sha256(
            json.dumps(feature_hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def build_features(
    features: list,
    panel: MarketPanel,
    *,
    timeframe: str,
    start,
    end,
    transforms: dict,
    feature_schema_version: str | None = None,
    adapter: FundamentalDataAdapter | None = None,
) -> tuple[dict[str, pd.Series], dict]:
    """Build fixed market features and strict-PIT fundamental features."""
    specs = validate_feature_specs(features, feature_schema_version)
    observations = _read_strict_observations(
        specs, start=start, end=end, adapter=adapter
    )
    output = _materialize_feature_series(
        specs, panel, timeframe=timeframe, transforms=transforms,
        observations=observations,
    )
    provenance = _feature_provenance(output, observations, feature_schema_version)
    return output, provenance
