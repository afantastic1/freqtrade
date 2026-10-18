'''
registry = default_registry()

features = build_features(
    market=data,
    specs=[
        FactorSpec("momentum", {"periods": 4}),
        FactorSpec("momentum", {"periods": 12}),
    ],
    registry=registry,
)
'''

import numpy as np
import pandas as pd

from alpha_lab.data.dataset import MarketData
from alpha_lab.factors.registry import (
    FactorSpec,
    FactorRegistry,
)


def build_features(
    market: MarketData,
    specs: list[FactorSpec],
    registry: FactorRegistry,
) -> pd.DataFrame:
    if not specs:
        raise ValueError("At least one factor is required")

    columns = {}

    for spec in specs:
        name = spec.output_name

        if name in columns:
            raise ValueError(f"Duplicate factor column: {name}")

        series = registry.compute(market, spec)
        columns[name] = series

    features = pd.DataFrame(
        columns,
        index=market.frame.index,
    )

    return features.replace(
        [np.inf, -np.inf],
        np.nan,
    )
