
import json

from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset

from alpha_lab.factors.registry import (
    FactorSpec,
    default_registry,
)
from alpha_lab.factors.builder import build_features
from alpha_lab.factors.candidate_loader import load_candidate

from alpha_lab.evaluation.incremental import evaluate_incremental

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
def main():
    candidate_dir = (
        "user_data/research/alpha_lab/"
        "candidates/volume_divergence"
    )

    # 1. 加载 BTC 1h 历史行情
    market = load_ohlcv(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        datadir="user_data/data/binance",
        start="2022-01-01",
        end="2025-01-01",
    )

    # 2. 注册内置因子和候选因子
    registry = default_registry()

    candidate, candidate_func = load_candidate(
        candidate_dir
    )

    registry.register(
        candidate.name,
        candidate_func,
    )

    # 3. 构建因子矩阵
    features = build_features(
        market=market,
        specs=[
            FactorSpec(
                name="momentum",
                params={"periods": 4},
            ),
            FactorSpec(
                name=candidate.name,
                params=candidate.parameters,
            ),
        ],
        registry=registry,
    )

    baseline_column = "momentum_periods_4"

    candidate_column = (
        "volume_divergence_"
        "momentum_periods_4_volume_periods_24"
    )

    # 4. 构建统一研究数据
    dataset = build_research_dataset(
        market=market,
        features=features,
        horizon=candidate.prediction_horizon,
    )

    # 5. 运行完整增量评价
    result = evaluate_incremental(
        dataset=dataset,
        candidate_column=candidate_column,
        baseline_columns=[baseline_column],
        test_start="2023-01-01",
        test_end="2025-01-01",
        train_months=12,
        retrain_months=1,
    )
    
    # 固定本次候选源码身份
    source_hash = hashlib.sha256(
        candidate.source_path.read_bytes()
    ).hexdigest()

    if source_hash != candidate.source_hash:
        raise ValueError(
            "Candidate source changed during evaluation"
        )

    report = {
        "schema_version": 1,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "candidate": {
            "name": candidate.name,
            "source_hash": candidate.source_hash,
            "hypothesis": candidate.hypothesis,
            "parameters": candidate.parameters,
            "prediction_horizon": candidate.prediction_horizon,
        },
        "experiment": {
            "symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "data_start": "2022-01-01",
            "data_end": "2025-01-01",
            "test_start": "2023-01-01",
            "test_end": "2025-01-01",
            "baseline_factors": [baseline_column],
            "candidate_factor": candidate_column,
        },
        "evaluation": result,
    }

    # JSON 标准不接受 NaN/Infinity。
    def json_safe(value):
        if isinstance(value, dict):
            return {
                str(k): json_safe(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [json_safe(v) for v in value]
        if isinstance(value, np.generic):
            return json_safe(value.item())
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    # 使用完整因子指纹组织结果。
    # 不覆盖同一源码的历史运行。
    from uuid import uuid4

    report_dir = (
        Path("user_data/research/alpha_lab")
        / "incremental_reports"
        / candidate.name
    )

    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / (
        f"{candidate.source_hash[:12]}_"
        f"{uuid4().hex}.json"
    )

    with report_path.open("x", encoding="utf-8") as f:
        json.dump(
            json_safe(report),
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )

    print(f"\nReport saved: {report_path}")
    print(
        "Robust positive:",
        result["stability"]["robust_positive"],
    )

    
    print("\n=== FACTOR SIMILARITY ===")

    for name, metrics in result["factor_similarity"].items():
        print(f"\nBaseline: {name}")
        print(f"Pearson:  {metrics['pearson']:.6f}")
        print(f"Spearman: {metrics['spearman']:.6f}")
        print(f"Samples:  {metrics['n']}")
    # 6. 输出全部结果
    print("\n=== INCREMENTAL FACTOR REPORT ===")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
