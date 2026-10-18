
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from alpha_lab.factors.registry import FactorSpec


# 第一版仅允许使用这些日期作为研究范围。
# 这只是 Runner 的逻辑限制，不是文件系统沙箱。
MIN_DATE = pd.Timestamp("2022-01-01", tz="UTC")
MAX_DATE = pd.Timestamp("2025-01-01", tz="UTC")


@dataclass(frozen=True)
class ExperimentConfig:
    symbol: str
    timeframe: str
    start: str
    end: str
    horizon: int
    factors: tuple[FactorSpec, ...]
    model: str = "logistic"
    train_months: int = 12
    retrain_months: int = 1
    test_start: str = "2023-01-01"
    test_end: str = "2025-01-01"

    def __post_init__(self):
        if self.symbol != "BTC/USDT:USDT":
            raise ValueError("Unsupported symbol")

        if self.timeframe not in ("1h", "15m"):
            raise ValueError("Unsupported timeframe")

        if type(self.horizon) is not int or self.horizon <= 0:
            raise ValueError("Invalid horizon")

        if self.model != "logistic":
            raise ValueError("Unsupported model")

        if (
            type(self.train_months) is not int
            or self.train_months <= 0
            or type(self.retrain_months) is not int
            or self.retrain_months <= 0
        ):
            raise ValueError("Invalid training window")


        if len(self.factors) > 20:
            raise ValueError("Too many factors")

        def parse_date(value):
            ts = pd.Timestamp(value)

            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")

            if ts != ts.normalize():
                raise ValueError("Date must be UTC midnight")

            return ts

        start = parse_date(self.start)
        end = parse_date(self.end)
        test_start = parse_date(self.test_start)
        test_end = parse_date(self.test_end)

        if not (MIN_DATE <= start < end <= MAX_DATE):
            raise ValueError("Research date outside allowed range")

        if not (
            start < test_start < test_end <= end
        ):
            raise ValueError("Invalid test window")

        if test_start < start + pd.DateOffset(
            months=self.train_months
        ):
            raise ValueError("Insufficient training history")

    @classmethod
    def from_json(cls, path: str | Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))

        allowed = {
            "symbol", "timeframe", "start", "end",
            "horizon", "factors", "model",
            "train_months", "retrain_months",
            "test_start", "test_end",
        }

        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")

        specs = []

        for item in raw["factors"]:
            if set(item) - {"name", "params", "alias"}:
                raise ValueError("Unknown factor configuration")

            params = item.get("params", {})

            if not isinstance(params, dict):
                raise ValueError("Factor params must be a dict")

            # 第一版严格限制参数类型。
            for value in params.values():
                if type(value) not in (int, float, str, bool):
                    raise ValueError("Unsupported factor parameter")

            specs.append(FactorSpec(
                name=item["name"],
                params=dict(params),
                alias=item.get("alias"),
            ))

        raw["factors"] = tuple(specs)
        return cls(**raw)
