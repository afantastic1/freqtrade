"""Immutable authorization policies for ResearchRequest v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Protocol

from .data_types import is_data_type

CAMPAIGNS_ROOT = Path(__file__).with_name("campaigns")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SPLITS = ("discovery", "validation", "holdout")
_BOUNDS = ("lookback_bars", "label_horizon_bars", "embargo_bars")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a nonempty RFC 3339 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CampaignPolicyV1:
    campaign_id: str
    policy_version: str
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    modes: tuple[str, ...]
    data_types: tuple[str, ...]
    splits: Mapping[str, TimeWindow]
    parameter_bounds: Mapping[str, tuple[int, int]]
    policy_sha256: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "CampaignPolicyV1":
        expected = {"protocol_version", "campaign_id", "policy_version", "policy_sha256", "symbols", "timeframes", "modes", "data_types", "splits", "parameter_bounds"}
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise ValueError("policy contains missing or unknown fields")
        if raw["protocol_version"] != "1.0-campaign-policy":
            raise ValueError("unsupported policy protocol_version")
        campaign_id, version = raw["campaign_id"], raw["policy_version"]
        if not isinstance(campaign_id, str) or not _ID_RE.fullmatch(campaign_id):
            raise ValueError("invalid campaign_id")
        if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
            raise ValueError("invalid policy_version")
        supplied_hash = raw["policy_sha256"]
        if not isinstance(supplied_hash, str) or not _HASH_RE.fullmatch(supplied_hash):
            raise ValueError("invalid policy_sha256")
        lists: dict[str, tuple[str, ...]] = {}
        for key in ("symbols", "timeframes", "modes", "data_types"):
            value = raw[key]
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value) or len(value) != len(set(value)):
                raise ValueError(f"{key} must be a nonempty list of unique strings")
            lists[key] = tuple(value)
        if any(mode not in {"time_series", "cross_sectional"} for mode in lists["modes"]):
            raise ValueError("unsupported mode")
        if any(not is_data_type(item) for item in lists["data_types"]):
            raise ValueError("unsupported data type")
        split_raw = raw["splits"]
        if not isinstance(split_raw, Mapping) or set(split_raw) != set(_SPLITS):
            raise ValueError("splits must define discovery, validation, and holdout")
        splits: dict[str, TimeWindow] = {}
        previous_end: datetime | None = None
        for name in _SPLITS:
            window = split_raw[name]
            if not isinstance(window, Mapping) or set(window) != {"start", "end"}:
                raise ValueError("split window must contain start and end")
            start, end = _timestamp(window["start"]), _timestamp(window["end"])
            if start >= end or (previous_end is not None and start < previous_end):
                raise ValueError("split windows must be ordered, nonempty, and non-overlapping")
            splits[name] = TimeWindow(start, end)
            previous_end = end
        bounds_raw = raw["parameter_bounds"]
        if not isinstance(bounds_raw, Mapping) or set(bounds_raw) != set(_BOUNDS):
            raise ValueError("parameter_bounds must define all temporal parameters")
        bounds: dict[str, tuple[int, int]] = {}
        for key in _BOUNDS:
            item = bounds_raw[key]
            if not isinstance(item, Mapping) or set(item) != {"min", "max"}:
                raise ValueError("each parameter bound must define min and max")
            low, high = item["min"], item["max"]
            if type(low) is not int or type(high) is not int or low < (0 if key == "embargo_bars" else 1) or low > high:
                raise ValueError("invalid parameter bounds")
            bounds[key] = (low, high)
        policy = cls(campaign_id, version, **lists, splits=splits, parameter_bounds=bounds, policy_sha256=supplied_hash)
        if supplied_hash != policy.sha256():
            raise ValueError("policy_sha256 does not match canonical policy content")
        return policy

    def canonical_json(self) -> str:
        def stamp(value: datetime) -> str:
            return value.isoformat(timespec="seconds").replace("+00:00", "Z")
        raw = {
            "protocol_version": "1.0-campaign-policy", "campaign_id": self.campaign_id,
            "policy_version": self.policy_version, "symbols": list(self.symbols),
            "timeframes": list(self.timeframes), "modes": list(self.modes), "data_types": list(self.data_types),
            "splits": {name: {"start": stamp(self.splits[name].start), "end": stamp(self.splits[name].end)} for name in _SPLITS},
            "parameter_bounds": {name: {"min": self.parameter_bounds[name][0], "max": self.parameter_bounds[name][1]} for name in _BOUNDS},
        }
        return _canonical(raw)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class CampaignPolicyRegistry(Protocol):
    def get(self, campaign_id: str, policy_version: str, policy_sha256: str) -> CampaignPolicyV1: ...


class FileCampaignPolicyRegistry:
    """Reads policies only from the package's fixed campaigns directory."""

    def get(self, campaign_id: str, policy_version: str, policy_sha256: str) -> CampaignPolicyV1:
        if not isinstance(campaign_id, str) or not _ID_RE.fullmatch(campaign_id) or not isinstance(policy_version, str) or not re.fullmatch(r"[1-9][0-9]*", policy_version):
            raise ValueError("invalid campaign identity")
        if not isinstance(policy_sha256, str) or not _HASH_RE.fullmatch(policy_sha256):
            raise ValueError("invalid policy hash")
        path = CAMPAIGNS_ROOT / f"{campaign_id}-v{policy_version}.json"
        if path.is_symlink():
            raise ValueError("policy records may not be symlinks")
        raw = json.loads(path.read_text(encoding="utf-8"))
        policy = CampaignPolicyV1.from_dict(raw)
        if (policy.campaign_id, policy.policy_version, policy.sha256()) != (campaign_id, policy_version, policy_sha256):
            raise ValueError("registered policy identity or hash mismatch")
        return policy
