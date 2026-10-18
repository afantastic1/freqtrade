
import importlib.util
import sys
from pathlib import Path

import pandas as pd

from alpha_lab.factors.candidate import (
    CandidateSpec,
    submit_candidate,
)


def load_candidate(
    candidate_dir: str | Path,
):
    """
    加载 Agent 自主提交的 Python 因子。

    candidate_dir 由调用者指定。
    """
    spec: CandidateSpec = submit_candidate(candidate_dir)

    module_name = f"alpha_candidate_{spec.source_hash}"

    module_spec = importlib.util.spec_from_file_location(
        module_name,
        spec.source_path,
    )

    if module_spec is None or module_spec.loader is None:
        raise ImportError("Cannot load candidate")

    module = importlib.util.module_from_spec(module_spec)

    try:
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    compute = getattr(module, "compute", None)

    if not callable(compute):
        raise TypeError("Candidate must define compute()")

    def factor_function(market, **params):
        frame = market.frame.copy(deep=True)

        # 向 Agent 因子传入统一 OHLCV 输入
        result = compute(frame, **params)

        if not isinstance(result, pd.Series):
            raise TypeError("Factor must return pandas Series")

        if not result.index.equals(frame.index):
            raise ValueError("Factor index mismatch")

        if not pd.api.types.is_numeric_dtype(result):
            raise TypeError("Factor must return numeric values")

        return result

    return spec, factor_function
