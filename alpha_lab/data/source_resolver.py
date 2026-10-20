"""Resolve only explicitly authorized, fully identified Catalog v3 sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

import pandas as pd

from data_engineering.catalog_v3 import list_datasets, read_dataset_snapshot, validate_dataset
from data_engineering.sources.contracts import DatasetIdentity
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from .availability import AvailabilityRuleRegistry, assign_availability
from .temporal_plan import DatasetBuildOptionsV1, TemporalPlanV1


@dataclass(frozen=True)
class ResolvedSourceV1:
    identity: DatasetIdentity
    manifest_sha256: str
    observations: pd.DataFrame
    availability: pd.DataFrame
    quality: dict

    def __post_init__(self):
        object.__setattr__(self, "observations", self.observations.copy(deep=True))
        object.__setattr__(self, "availability", self.availability.copy(deep=True))
        object.__setattr__(self, "quality", dict(self.quality))


_TYPE = {
    "futures_ohlcv": ("futures", "ohlcv"), "spot_ohlcv": ("spot", "ohlcv"),
    "funding_rate": ("futures", "funding_rate"), "open_interest": ("futures", "open_interest"),
}


class DatasetSourceResolver:
    def __init__(
        self, catalog_root, source_roots: Mapping[str, object], *, exchange: str,
        symbol_identities: Mapping[str, Mapping[str, str]],
        availability_registry: AvailabilityRuleRegistry,
    ):
        self.catalog_root = catalog_root
        self.source_roots = dict(source_roots)
        self.exchange = exchange
        self.symbol_identities = {key: dict(value) for key, value in symbol_identities.items()}
        self.availability_registry = availability_registry

    def _expected_identity(self, symbol: str, data_type: str, timeframe: str) -> DatasetIdentity:
        if symbol not in self.symbol_identities or data_type not in _TYPE:
            raise ValueError("request symbol or data type has no trusted source identity mapping")
        market_type, data_kind = _TYPE[data_type]
        mapped = self.symbol_identities[symbol]
        if mapped.get("exchange") != self.exchange or mapped.get("market_type") != market_type:
            raise ValueError("configured source identity does not match requested data type")
        return DatasetIdentity(
            exchange=self.exchange, market_type=market_type, asset=mapped["asset"],
            exchange_symbol=mapped["exchange_symbol"], data_kind=data_kind,
            timeframe=timeframe if data_kind == "ohlcv" else mapped.get("timeframe", "event"),
        )

    def resolve(self, request: ResearchRequestV2, plan: TemporalPlanV1, options: DatasetBuildOptionsV1) -> tuple[ResolvedSourceV1, ...]:
        results = []
        for symbol in request.symbols:
            for data_type in request.data_types:
                identity = self._expected_identity(symbol, data_type, request.timeframe)
                candidates = [record for record in list_datasets(self.catalog_root)
                              if all(record["manifest"].get(field) == getattr(identity, field)
                                     for field in ("exchange", "market_type", "asset", "exchange_symbol", "data_kind", "timeframe"))]
                if not candidates:
                    continue
                dataset_ids = {record["manifest"]["dataset_id"] for record in candidates}
                if len(dataset_ids) != 1:
                    raise ValueError("ambiguous Catalog dataset identity")
                dataset_id = next(iter(dataset_ids))
                requested_pin = options.source_snapshot_pins.get(dataset_id)
                if requested_pin is None:
                    latest = validate_dataset(self.catalog_root, dataset_id, source_roots=self.source_roots)
                    if latest["status"] == "INVALID" or latest["manifest"] is None:
                        raise ValueError("latest Catalog dataset is invalid")
                    pin = next(item["manifest_sha256"] for item in candidates
                               if item["manifest"] == latest["manifest"])
                else:
                    pin = requested_pin
                snapshot = read_dataset_snapshot(
                    self.catalog_root, dataset_id, pin, plan.warmup_start,
                    plan.label_extension_end, source_roots=self.source_roots,
                )
                rule_id = options.availability_rule_ids.get(data_type)
                if rule_id is None:
                    raise ValueError(f"availability rule not configured for {data_type}")
                rule = self.availability_registry.get(rule_id, dataset_identity=identity)
                available = assign_availability(snapshot.observations, identity, pin, rule, None)
                results.append(ResolvedSourceV1(identity, pin, snapshot.observations, available, snapshot.quality))
        return tuple(results)
