"""Strict ResearchRequest v2 parser and policy-aware validator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Mapping

from ._timestamps import parse_rfc3339_utc
from .campaign_policy_v1 import CampaignPolicyRegistry
from .data_types import is_data_type

_FIELDS = {
    "protocol_version", "request_id", "campaign_id", "policy_version", "campaign_policy_sha256",
    "start", "end", "symbols", "timeframe", "mode", "data_types", "split", "lookback_bars",
    "label_horizon_bars", "embargo_bars",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SPLITS = {"discovery", "validation", "holdout"}


def _parse_timestamp(value: object, name: str) -> datetime:
    return parse_rfc3339_utc(value, name)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ResearchRequestV2:
    request_id: str
    campaign_id: str
    policy_version: str
    campaign_policy_sha256: str
    start: datetime
    end: datetime
    symbols: tuple[str, ...]
    timeframe: str
    mode: str
    data_types: tuple[str, ...]
    split: str
    lookback_bars: int
    label_horizon_bars: int
    embargo_bars: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], registry: CampaignPolicyRegistry) -> "ResearchRequestV2":
        if not isinstance(raw, Mapping) or set(raw) != _FIELDS:
            raise ValueError("request contains missing or unknown fields")
        if raw["protocol_version"] != "2.0-research-request":
            raise ValueError("unsupported protocol_version")
        request_id = raw["request_id"]
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid request_id")
        campaign_id, version, digest = raw["campaign_id"], raw["policy_version"], raw["campaign_policy_sha256"]
        if not isinstance(campaign_id, str) or not isinstance(version, str) or not isinstance(digest, str):
            raise ValueError("campaign identity fields must be strings")
        policy = registry.get(campaign_id, version, digest)
        start, end = _parse_timestamp(raw["start"], "start"), _parse_timestamp(raw["end"], "end")
        if start >= end:
            raise ValueError("request window must satisfy start < end")
        symbols = raw["symbols"]
        if not isinstance(symbols, list) or not symbols or any(not isinstance(item, str) or not item for item in symbols) or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be a nonempty list of unique strings")
        timeframe = raw["timeframe"]
        if not isinstance(timeframe, str) or not timeframe:
            raise ValueError("timeframe must be a nonempty string")
        mode, split = raw["mode"], raw["split"]
        if mode not in {"time_series", "cross_sectional"}:
            raise ValueError("unsupported mode")
        if split not in _SPLITS:
            raise ValueError("unsupported split")
        data_types = raw["data_types"]
        if not isinstance(data_types, list) or not data_types or any(not isinstance(item, str) or not is_data_type(item) for item in data_types) or len(set(data_types)) != len(data_types):
            raise ValueError("data_types must be a nonempty list of unique registered IDs")
        for field in ("lookback_bars", "label_horizon_bars", "embargo_bars"):
            value = raw[field]
            minimum = 0 if field == "embargo_bars" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} must be an integer >= {minimum}")
            low, high = policy.parameter_bounds[field]
            if not low <= value <= high:
                raise ValueError(f"{field} is outside campaign policy bounds")
        split_window = policy.splits[split]
        if start < split_window.start or end > split_window.end:
            raise ValueError("request window is outside the selected campaign split")
        if not set(symbols).issubset(policy.symbols):
            raise ValueError("request contains symbols not authorized by campaign policy")
        if timeframe not in policy.timeframes:
            raise ValueError("timeframe is not authorized by campaign policy")
        if mode not in policy.modes:
            raise ValueError("mode is not authorized by campaign policy")
        if not set(data_types).issubset(policy.data_types):
            raise ValueError("request contains data types not authorized by campaign policy")
        return cls(request_id, campaign_id, version, digest, start, end, tuple(symbols), timeframe, mode, tuple(data_types), split, raw["lookback_bars"], raw["label_horizon_bars"], raw["embargo_bars"])

    def canonical_json(self) -> str:
        value = {
            "protocol_version": "2.0-research-request", "request_id": self.request_id,
            "campaign_id": self.campaign_id, "policy_version": self.policy_version,
            "campaign_policy_sha256": self.campaign_policy_sha256,
            "start": self.start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "end": self.end.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "symbols": list(self.symbols), "timeframe": self.timeframe, "mode": self.mode,
            "data_types": list(self.data_types), "split": self.split,
            "lookback_bars": self.lookback_bars, "label_horizon_bars": self.label_horizon_bars,
            "embargo_bars": self.embargo_bars,
        }
        return _canonical(value)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
