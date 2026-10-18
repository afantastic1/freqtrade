'''

这套代码可以让未来的 Agent 通过配置选择已注册因子，但不能动态导入任意 Python 代码。
例如：
factors = [    FactorSpec("momentum", {"periods": 4}),    FactorSpec("momentum", {"periods": 12}),    FactorSpec("momentum", {"periods": 24}),]



未来只需要注册新函数：
registry.register("realized_volatility", realized_volatility)registry.register("volume_surprise", volume_surprise)
'''

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.factors.momentum import momentum


FactorFunction = Callable[..., pd.Series]


@dataclass(frozen=True)
class FactorSpec:
    name: str
    params: dict = field(default_factory=dict)
    alias: str | None = None

    @property
    def output_name(self) -> str:
        if self.alias is not None:
            return self.alias

        if not self.params:
            return self.name

        args = "_".join(
            f"{key}_{value}"
            for key, value in sorted(self.params.items())
        )

        return f"{self.name}_{args}"


class FactorRegistry:
    def __init__(self):
        self._factors: dict[str, FactorFunction] = {}

    def register(
        self,
        name: str,
        func: FactorFunction,
    ) -> None:
        if name in self._factors:
            raise ValueError(f"Factor already registered: {name}")

        self._factors[name] = func

    def compute(
        self,
        market: MarketData,
        spec: FactorSpec,
    ) -> pd.Series:
        if spec.name not in self._factors:
            raise ValueError(f"Unknown factor: {spec.name}")

        func = self._factors[spec.name]
        result = func(market, **spec.params)

        if not isinstance(result, pd.Series):
            raise TypeError("Factor must return a Series")

        if not result.index.equals(market.frame.index):
            raise ValueError("Factor index does not match market")

        return result.rename(spec.output_name)


def default_registry() -> FactorRegistry:
    registry = FactorRegistry()
    registry.register("momentum", momentum)
    return registry
