"""Bridge verified fundamentals into Alpha Lab Dataset feature inputs."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel

from .fundamentals import _validated_max_age, asof_join
from .registries import AssetRegistry, MetricRegistry


def build_fundamental_features(
    panel: MarketPanel,
    observations: pd.DataFrame,
    *,
    metrics: Sequence[str],
    max_age: str,
) -> dict[str, pd.Series]:
    """Build PIT-aligned Series for `CrossSectionalDataset(features=...)`.

    Missing asset/metric/date combinations remain NaN. Every output Series
    carries the registry and snapshot identities used to create it.
    """
    if not metrics:
        raise ValueError("at least one fundamental metric is required")
    _validated_max_age(max_age)
    if len(metrics) != len(set(metrics)):
        raise ValueError("duplicate metric requested")
    metric_registry = MetricRegistry()
    asset_registry = AssetRegistry()
    features = {}
    for metric in metrics:
        definition = metric_registry.resolve_metric(metric)
        name = f"fundamental__{definition.entity_scope}__{metric}"
        if name in features:
            raise ValueError(f"duplicate feature identity: {name}")
        selected = observations.loc[observations.metric.eq(metric)]
        if selected.empty:
            values = pd.Series(np.nan, index=panel.frame.index, name=name, dtype=float)
            used = selected
        else:
            joined = asof_join(panel, observations, metric=metric, max_age=max_age)
            values = joined.fundamental_value.astype(float).rename(name)
            used = selected
        provenance = used if not used.empty else observations
        values.attrs.update({
            "asset_registry_version": asset_registry.version,
            "metric_registry_version": metric_registry.version,
            "pit_status": "VERIFIED" if not used.empty else "NOT_EVALUABLE",
            "dataset_ids": sorted(provenance.dataset_id.dropna().astype(str).unique()),
            "snapshot_sha256": sorted(provenance.snapshot_sha256.dropna().astype(str).unique()),
            "sources": sorted(provenance.source.dropna().astype(str).unique()),
            "snapshot_pins": observations.attrs.get("snapshot_pins", []),
            "pit_attestation_sha256": observations.attrs.get("pit_attestation_sha256", []),
            "max_age": max_age,
        })
        features[name] = values
    return features
