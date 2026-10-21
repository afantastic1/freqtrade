"""Split-bounded temporal planning and explicit sample partitioning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from alpha_lab_contracts.campaign_policy_v1 import CampaignPolicyV1
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab.data.timeframe import timeframe_to_seconds


@dataclass(frozen=True)
class TemporalPlanV1:
    split_start: datetime
    split_end: datetime
    study_start: datetime
    study_end: datetime
    warmup_start: datetime
    warmup_end: datetime
    label_extension_start: datetime
    label_extension_end: datetime
    timeframe: str
    label_extension_truncated: bool = False
    warmup_truncated_bars: int = 0


def build_temporal_plan(request: ResearchRequestV2, policy: CampaignPolicyV1,
                        *, required_history_bars: int = 0) -> TemporalPlanV1:
    if (request.campaign_id, request.policy_version, request.campaign_policy_sha256) != (
        policy.campaign_id, policy.policy_version, policy.sha256()
    ):
        raise ValueError("request campaign policy identity mismatch")
    split = policy.splits.get(request.split)
    if split is None or request.start < split.start or request.end > split.end:
        raise ValueError("request interval outside selected split")
    step = timedelta(seconds=timeframe_to_seconds(request.timeframe))
    if type(required_history_bars) is not int or required_history_bars < 0:
        raise ValueError("required_history_bars must be a nonnegative integer")
    history_bars = max(request.lookback_bars, required_history_bars)
    # OHLCV decisions occur after bar close; include the source slot that
    # supplies the first in-range decision plus the required history before it.
    warmup_start = max(split.start, request.start - (history_bars + 1) * step)
    available_warmup_bars = max(0, int((request.start - warmup_start) / step) - 1)
    requested_label_end = request.end + request.label_horizon_bars * step
    label_end = min(requested_label_end, split.end)
    return TemporalPlanV1(
        split_start=split.start, split_end=split.end,
        study_start=request.start, study_end=request.end,
        warmup_start=warmup_start, warmup_end=request.start,
        label_extension_start=request.start, label_extension_end=label_end,
        timeframe=request.timeframe, label_extension_truncated=requested_label_end > split.end,
        warmup_truncated_bars=max(0, history_bars - available_warmup_bars),
    )


@dataclass(frozen=True)
class DatasetBuildOptionsV1:
    missing_data_policy: str
    primary_data_type: str | None
    source_snapshot_pins: dict[str, str]
    availability_rule_ids: dict[str, str]
    max_age_by_data_type: dict[str, int]

    def __post_init__(self):
        if self.missing_data_policy not in {"reject", "preserve"}:
            raise ValueError("missing_data_policy must be reject or preserve")
        for name in ("source_snapshot_pins", "availability_rule_ids", "max_age_by_data_type"):
            object.__setattr__(self, name, dict(getattr(self, name)))
        if any(type(value) is not int or value <= 0 for value in self.max_age_by_data_type.values()):
            raise ValueError("max ages must be positive integer seconds")


def enforce_required_slots(expected: pd.DatetimeIndex, observed: pd.DatetimeIndex, policy: str) -> pd.DatetimeIndex:
    missing = expected.difference(observed)
    if len(missing) and policy == "reject":
        raise ValueError(f"missing required observation slots: {len(missing)}")
    if policy not in {"reject", "preserve"}:
        raise ValueError("missing data policy must be reject or preserve")
    return missing


def preserve_sparse_events(events: pd.DataFrame) -> pd.DataFrame:
    """Return sparse event observations as-is; never expand them onto a candle grid."""
    return events.copy(deep=True)


def partition_temporal_samples(
    labels: pd.DataFrame, *, train_end: pd.Timestamp, test_end: pd.Timestamp,
    timeframe: str, embargo_bars: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if type(embargo_bars) is not int or embargo_bars < 0:
        raise ValueError("embargo_bars must be a nonnegative integer")
    if not {"decision_time", "label_available_time"}.issubset(labels.columns):
        raise ValueError("labels require decision_time and label_available_time")
    if train_end.tzinfo is None or test_end.tzinfo is None or train_end >= test_end:
        raise ValueError("partition bounds must be ordered and timezone-aware")
    frame = labels.copy(deep=True)
    for column in ("decision_time", "label_available_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    step = pd.Timedelta(seconds=timeframe_to_seconds(timeframe))
    test_start = train_end + embargo_bars * step
    train = frame.loc[(frame.decision_time < train_end) & (frame.label_available_time <= train_end)].copy()
    test = frame.loc[(frame.decision_time >= test_start) & (frame.decision_time < test_end)].copy()
    return train.reset_index(drop=True), test.reset_index(drop=True)
