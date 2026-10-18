
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from alpha_lab.data.dataset import MarketData


@dataclass(frozen=True)
class FactorValidationReport:
    passed: bool
    errors: tuple[str, ...]
    valid_count: int
    total_count: int


class FactorValidator:
    def __init__(
        self,
        min_valid_ratio: float = 0.8,
        prefix_points: int = 5,
    ):
        if not 0 < min_valid_ratio <= 1:
            raise ValueError("Invalid min_valid_ratio")

        if prefix_points < 1:
            raise ValueError("Invalid prefix_points")

        self.min_valid_ratio = min_valid_ratio
        self.prefix_points = prefix_points

    @staticmethod
    def _check_output(
        result,
        expected_index: pd.Index,
    ) -> np.ndarray:
        if not isinstance(result, pd.Series):
            raise TypeError("Factor must return pd.Series")

        if not result.index.equals(expected_index):
            raise ValueError("Factor index mismatch")

        if not pd.api.types.is_numeric_dtype(result.dtype):
            raise TypeError("Factor must be numeric")

        values = result.to_numpy(dtype=float)

        if np.isinf(values).any():
            raise ValueError("Factor contains infinity")

        return values

    def validate(
        self,
        market: MarketData,
        factor: Callable[..., pd.Series],
        params: dict | None = None,
    ) -> FactorValidationReport:
        params = dict(params or {})
        errors = []
        frame = market.frame

        def compute(current: MarketData):
            before = current.frame.copy(deep=True)

            result = factor(current, **params)

            pd.testing.assert_frame_equal(
                current.frame,
                before,
                check_exact=True,
            )

            values = self._check_output(
                result,
                current.frame.index,
            )

            return values

        try:
            full = compute(market)

            valid_count = int(np.isfinite(full).sum())
            total_count = len(full)

            if total_count == 0:
                raise ValueError("Empty market data")

            ratio = valid_count / total_count

            if ratio < self.min_valid_ratio:
                errors.append(
                    f"Insufficient factor coverage: {ratio:.2%}"
                )

            # 重复计算应产生相同结果
            repeated = compute(market)

            np.testing.assert_allclose(
                full,
                repeated,
                rtol=1e-10,
                atol=1e-12,
                equal_nan=True,
            )

            # 从不同历史时刻截断数据
            # 检查完整数据是否影响此前已经算出的因子
            if total_count >= 10:
                cutoffs = np.unique(
                    np.linspace(
                        max(2, total_count // 5),
                        total_count - 1,
                        num=self.prefix_points,
                        dtype=int,
                    )
                )

                for cutoff in cutoffs:
                    short_frame = frame.iloc[:cutoff].copy()

                    short_market = MarketData(
                        symbol=market.symbol,
                        timeframe=market.timeframe,
                        frame=short_frame,
                    )

                    short = compute(short_market)

                    np.testing.assert_allclose(
                        short,
                        full[:cutoff],
                        rtol=1e-10,
                        atol=1e-12,
                        equal_nan=True,
                        err_msg=(
                            f"Prefix invariance failed at {cutoff}"
                        ),
                    )

        except Exception as exc:
            errors.append(
                f"{type(exc).__name__}: {exc}"
            )

            valid_count = locals().get("valid_count", 0)
            total_count = len(frame)

        return FactorValidationReport(
            passed=not errors,
            errors=tuple(errors),
            valid_count=valid_count,
            total_count=total_count,
        )
