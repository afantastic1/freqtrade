"""Versioned source availability evidence and point-in-time joins."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from data_engineering.sources.contracts import DatasetIdentity


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _utc_series(values: pd.Series, name: str) -> pd.Series:
    result = pd.to_datetime(values, errors="raise")
    if not isinstance(result.dtype, pd.DatetimeTZDtype):
        raise ValueError(f"{name} must contain timezone-aware timestamps")
    return result.dt.tz_convert("UTC")


@dataclass(frozen=True)
class AvailabilityRuleV1:
    rule_id: str
    dataset_identity: DatasetIdentity
    mode: str
    event_time_field: str
    fixed_delay: timedelta | None = None
    max_age: timedelta | None = None
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.rule_id or not self.event_time_field or not isinstance(self.dataset_identity, DatasetIdentity):
            raise ValueError("availability rule identity and event field are required")
        if self.mode not in {"source_evidence", "fixed_delay"}:
            raise ValueError("unsupported availability rule mode")
        if self.mode == "fixed_delay" and (self.fixed_delay is None or self.fixed_delay < timedelta(0)):
            raise ValueError("fixed_delay mode requires a nonnegative delay")
        if self.mode == "source_evidence" and self.fixed_delay is not None:
            raise ValueError("source_evidence rule cannot define fixed_delay")
        if self.max_age is not None and self.max_age <= timedelta(0):
            raise ValueError("max_age must be positive")

    @property
    def rule_sha256(self) -> str:
        raw = {
            "rule_id": self.rule_id, "version": self.version,
            "dataset_identity": {
                "exchange": self.dataset_identity.exchange,
                "market_type": self.dataset_identity.market_type,
                "asset": self.dataset_identity.asset,
                "exchange_symbol": self.dataset_identity.exchange_symbol,
                "data_kind": self.dataset_identity.data_kind,
                "timeframe": self.dataset_identity.timeframe,
            },
            "mode": self.mode, "event_time_field": self.event_time_field,
            "fixed_delay_seconds": self.fixed_delay.total_seconds() if self.fixed_delay else None,
            "max_age_seconds": self.max_age.total_seconds() if self.max_age else None,
        }
        return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AvailabilityEvidenceV1:
    dataset_id: str
    manifest_sha256: str
    rows: pd.DataFrame
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.manifest_sha256):
            raise ValueError("evidence manifest digest must be SHA-256")
        rows = self.rows.copy(deep=True)
        if not {"row_key", "available_time"}.issubset(rows.columns):
            raise ValueError("evidence rows require row_key and available_time")
        if rows.row_key.isna().any() or rows.row_key.duplicated().any():
            raise ValueError("evidence row keys must be unique and non-null")
        rows["available_time"] = _utc_series(rows.available_time, "available_time")
        if rows.available_time.isna().any():
            raise ValueError("evidence availability times cannot be null")
        if self.evidence_sha256 is None:
            canonical = rows.assign(available_time=rows.available_time.astype(str)).sort_values("row_key").to_json(orient="records")
            object.__setattr__(self, "evidence_sha256", hashlib.sha256(canonical.encode()).hexdigest())
        elif not _DIGEST.fullmatch(self.evidence_sha256):
            raise ValueError("evidence digest must be SHA-256")
        object.__setattr__(self, "rows", rows)


class AvailabilityRuleRegistry:
    def __init__(self, rules: Mapping[str, AvailabilityRuleV1]):
        copied = dict(rules)
        if any(key != value.rule_id for key, value in copied.items()):
            raise ValueError("availability rule registry keys must match rule IDs")
        self._rules = MappingProxyType(copied)

    def get(self, rule_id: str, *, dataset_identity: DatasetIdentity) -> AvailabilityRuleV1:
        try:
            rule = self._rules[rule_id]
        except KeyError as exc:
            raise ValueError(f"unknown availability rule: {rule_id}") from exc
        if rule.dataset_identity != dataset_identity:
            raise ValueError("availability rule dataset identity mismatch")
        return rule


def assign_availability(
    observations: pd.DataFrame,
    identity: DatasetIdentity,
    manifest_sha256: str,
    rule: AvailabilityRuleV1,
    evidence: AvailabilityEvidenceV1 | None,
) -> pd.DataFrame:
    if rule.dataset_identity != identity:
        raise ValueError("availability rule dataset identity mismatch")
    if not _DIGEST.fullmatch(manifest_sha256):
        raise ValueError("manifest digest must be SHA-256")
    frame = observations.copy(deep=True).reset_index(drop=True)
    if rule.event_time_field not in frame:
        raise ValueError("event timestamp field is missing")
    frame["event_time"] = _utc_series(frame[rule.event_time_field], "event_time")
    frame["availability_rule_id"] = rule.rule_id
    frame["availability_rule_version"] = rule.version
    frame["availability_rule_sha256"] = rule.rule_sha256
    frame["availability_manifest_sha256"] = manifest_sha256
    if rule.mode == "fixed_delay":
        if "close_time" in frame:
            basis = _utc_series(frame["close_time"], "close_time")
        elif identity.data_kind == "ohlcv":
            from freqtrade.exchange import timeframe_to_seconds
            basis = frame.event_time + pd.Timedelta(seconds=timeframe_to_seconds(identity.timeframe))
        else:
            basis = frame.event_time
        frame["available_time"] = basis + pd.Timedelta(rule.fixed_delay)
        frame["pit_status"] = "ASSUMED"
    elif evidence is None:
        frame["available_time"] = pd.NaT
        frame["pit_status"] = "UNVERIFIED"
    else:
        if evidence.dataset_id != identity.dataset_id() or evidence.manifest_sha256 != manifest_sha256:
            raise ValueError("availability evidence does not match pinned dataset manifest")
        expected = pd.DataFrame({"row_key": list(range(len(frame)))})
        evidence_rows = evidence.rows
        if len(evidence_rows) != len(frame) or set(evidence_rows.row_key) != set(expected.row_key):
            raise ValueError("availability evidence must cover every observation row exactly once")
        evidence_rows = evidence_rows.set_index("row_key")
        frame["available_time"] = pd.to_datetime(
            [evidence_rows.loc[index, "available_time"] for index in range(len(frame))], utc=True
        )
        frame["pit_status"] = "EVIDENCED"
        frame["availability_evidence_sha256"] = evidence.evidence_sha256
    return frame


def asof_join(
    decisions: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    decision_time: str,
    max_age: pd.Timedelta | None = None,
) -> pd.DataFrame:
    if decision_time not in decisions or "available_time" not in observations:
        raise ValueError("decision and availability timestamps are required")
    left = decisions.copy(deep=True).reset_index(drop=False).rename(columns={"index": "_decision_index"})
    right = observations.copy(deep=True)
    if "pit_status" in right and right.pit_status.eq("UNVERIFIED").any():
        raise ValueError("UNVERIFIED observations cannot enter worker view")
    left[decision_time] = _utc_series(left[decision_time], decision_time)
    right["available_time"] = _utc_series(right.available_time, "available_time")
    if right.available_time.isna().any():
        raise ValueError("UNVERIFIED observations cannot enter worker view")
    left = left.sort_values(decision_time)
    right = right.dropna(subset=["available_time"]).sort_values("available_time")
    joined = pd.merge_asof(
        left, right, left_on=decision_time, right_on="available_time", direction="backward",
        tolerance=None,
    )
    if max_age is not None and "event_time" in joined:
        joined.loc[joined[decision_time] - joined["event_time"] > max_age, right.columns] = pd.NA
    return joined.sort_values("_decision_index").drop(columns=["_decision_index"]).reset_index(drop=True)
