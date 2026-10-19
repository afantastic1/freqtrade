"""Separate multi-asset research dataset with UTC section splits."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from alpha_lab.data.market_panel import MarketPanel


@dataclass(frozen=True)
class CrossSectionalDataset:
    panel: MarketPanel
    membership: pd.Series
    features: dict[str, pd.Series]
    labels: pd.DataFrame

    def __post_init__(self):
        index = self.panel.frame.index
        if not self.membership.index.equals(index) or self.membership.isna().any():
            raise ValueError("membership must align with panel")
        if not self.labels.index.equals(index):
            raise ValueError("labels must align with panel")
        if not self.labels.decision_time.equals(self.panel.frame.available_time.rename("decision_time")):
            raise ValueError("feature availability differs from label decision time")
        if not self.features:
            raise ValueError("at least one feature is required")
        for name, values in self.features.items():
            if not name or not values.index.equals(index):
                raise ValueError("feature must align with panel")

    def frame(self, *, target: str = "relative_return") -> pd.DataFrame:
        if target not in ("relative_return", "raw_return"):
            raise ValueError("unsupported target")
        features = pd.DataFrame(self.features, index=self.panel.frame.index)
        selected = features.join(self.labels[[target, "decision_time", "label_available_time"]])
        selected = selected.replace([np.inf, -np.inf], np.nan)
        selected = selected.loc[self.membership.astype(bool)]
        return selected.dropna(subset=list(self.features) + [target, "label_available_time"])

    def split(self, *, train_end, test_end, target: str = "relative_return", embargo=None):
        cutoff = pd.Timestamp(train_end)
        end = pd.Timestamp(test_end)
        if cutoff.tzinfo is None or end.tzinfo is None or cutoff >= end:
            raise ValueError("split times must be ordered UTC timestamps")
        cutoff = cutoff.tz_convert("UTC")
        end = end.tz_convert("UTC")
        data = self.frame(target=target)
        times = data.index.get_level_values("open_time")
        train = data.loc[(times < cutoff) & (data.label_available_time <= cutoff)]
        test_start = cutoff + (pd.Timedelta(embargo) if embargo is not None else pd.Timedelta(0))
        test = data.loc[(times >= test_start) & (times < end)]
        return train, test
