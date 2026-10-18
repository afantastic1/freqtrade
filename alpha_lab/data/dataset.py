
from dataclasses import dataclass
import numpy as np
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype


REQUIRED_COLUMNS = {
    "open_time",
    "close_time",
    "available_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
}

TIME_COLUMNS = (
    "open_time",
    "close_time",
    "available_time",
)

PRICE_COLUMNS = ("open", "high", "low", "close")
NUMERIC_COLUMNS = PRICE_COLUMNS + ("volume",)


@dataclass(frozen=True)
class MarketData:
    symbol: str
    timeframe: str
    frame: pd.DataFrame

    def __post_init__(self):
        # 1. 验证输入类型
        if not isinstance(self.frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")

        if not self.symbol or not self.timeframe:
            raise ValueError("symbol and timeframe cannot be empty")

        # 2. 防御性复制，避免修改调用者的原始 DataFrame
        df = self.frame.copy(deep=True)

        # 3. 检查必需列
        missing = REQUIRED_COLUMNS - set(df.columns)

        if missing:
            raise ValueError(f"Missing columns: {sorted(missing)}")

        if not df.columns.is_unique:
            raise ValueError("Duplicate column names")

        # 4. 验证时间类型，要求显式时区
        for col in TIME_COLUMNS:
            series = df[col]

            if series.isna().any():
                raise ValueError(f"{col} contains NaT")

            if not is_datetime64_any_dtype(series):
                raise ValueError(
                    f"{col} must be timezone-aware datetime"
                )

            if series.dt.tz is None:
                raise ValueError(
                    f"{col} must include timezone"
                )

            # 统一转换为 UTC
            df[col] = series.dt.tz_convert("UTC")

        # 5. 检查重复和乱序 K 线
        if df["open_time"].duplicated().any():
            raise ValueError("Duplicate open_time")

        if not df["open_time"].is_monotonic_increasing:
            raise ValueError("Open time must be monotonic")

        # 6. 检查时间逻辑
        if (
            df["open_time"] >= df["close_time"]
        ).any():
            raise ValueError("Invalid candle time range")

        if (
            df["available_time"] < df["close_time"]
        ).any():
            raise ValueError("Data available before close")

        # 7. 检查数值类型和有限性
        for col in NUMERIC_COLUMNS:
            try:
                values = pd.to_numeric(
                    df[col], errors="raise"
                ).to_numpy(dtype=float)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(
                    f"Invalid numeric value: {col}"
                ) from exc

            if not np.isfinite(values).all():
                raise ValueError(
                    f"Invalid numeric value: {col}"
                )

            df[col] = values

        # 8. 价格必须严格大于 0
        for col in PRICE_COLUMNS:
            if (df[col] <= 0).any():
                raise ValueError(
                    f"{col} must be greater than 0"
                )

        # 9. 成交量允许为 0，但不能为负
        if (df["volume"] < 0).any():
            raise ValueError(
                "volume must be non-negative"
            )

        # 检查 OHLC 价格关系

        # OHLC 价格一致性检查
        invalid_ohlc = (
            (df["low"] > df["high"])
            | (df["high"] < df["open"])
            | (df["high"] < df["close"])
            | (df["low"] > df["open"])
            | (df["low"] > df["close"])
        )

        if invalid_ohlc.any():
            raise ValueError("Invalid OHLC price relationship")

        # 11. 只在所有校验成功后保存
        object.__setattr__(self, "frame", df)

    def __len__(self) -> int:
        return len(self.frame)

    def asof(self, decision_time) -> "MarketData":
        """
        返回决策时刻已经可用的历史 K 线。

        约束：
            available_time <= decision_time

        注意：
            不允许使用没有时区的决策时间。
        """
        ts = pd.Timestamp(decision_time)

        if pd.isna(ts):
            raise ValueError("Invalid decision_time")

        if ts.tzinfo is None:
            raise ValueError(
                "decision_time must include timezone"
            )

        ts = ts.tz_convert("UTC")

        subset = self.frame.loc[
            self.frame["available_time"] <= ts
        ].copy()

        return MarketData(
            symbol=self.symbol,
            timeframe=self.timeframe,
            frame=subset,
        )
