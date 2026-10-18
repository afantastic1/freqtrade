
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from alpha_lab.config import ExperimentConfig
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset
from alpha_lab.evaluation.factor_report import evaluate_factor
from alpha_lab.factors.builder import build_features
from alpha_lab.factors.registry import default_registry
from alpha_lab.models.evaluator import evaluate_model
from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.factors.candidate import submit_candidate
from alpha_lab.factors.candidate_loader import load_candidate
from alpha_lab.factors.registry import FactorSpec
from alpha_lab.validation.factor_validator import FactorValidator
from alpha_lab.evaluation.incremental import evaluate_incremental
from alpha_lab.evaluation.feedback import build_research_feedback

DATA_DIR = Path("user_data/data/binance")
OUTPUT_ROOT = Path("user_data/research/alpha_lab/runs")
DB_PATH = Path("user_data/research/alpha_lab/experiments.sqlite3")
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_BLOCK_SIZES = (24, 72, 168)


def json_safe(value):
    """将 numpy/pandas 类型转换为标准 JSON 类型。"""
    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if np.isfinite(number) else None

    if isinstance(value, (np.bool_,)):
        return bool(value)

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if value is pd.NA or value is pd.NaT:
        return None

    return value


def _execute_experiment(
    config: ExperimentConfig,
    run_id: str,
    config_hash: str,
    candidate_dir: str | Path | None = None
) -> dict:
    """
    执行一次研究。

    只负责数据加载、因子评价、模型评价和报告保存。
    SQLite 状态由外层 run_experiment 管理。
    """

    print(f"Executing run: {run_id}")

    # --------------------------------------------------------
    # 1. 加载行情
    # --------------------------------------------------------
    market = load_ohlcv(
        symbol=config.symbol,
        timeframe=config.timeframe,
        datadir=DATA_DIR,
        start=config.start,
        end=config.end,
        data_format="feather",
    )

    start = pd.Timestamp(config.start).tz_localize("UTC")
    end = pd.Timestamp(config.end).tz_localize("UTC")

    if (
        (market.frame["open_time"] < start).any()
        or (market.frame["close_time"] > end).any()
    ):
        raise ValueError("Loader returned out-of-range data")

    # --------------------------------------------------------
    # 2. 构建因子矩阵
    # --------------------------------------------------------
    
    registry = default_registry()
    specs = list(config.factors)
    candidate_info = None
    candidate = None
    baseline_count = len(specs)

    if candidate_dir is not None:
        candidate, factor_function = load_candidate(candidate_dir)

        # 验证器检查因子输出、可重复性、历史前缀不变性
        validation = FactorValidator().validate(
            market=market,
            factor=factor_function,
            params=candidate.parameters,
        )

        if not validation.passed:
            raise ValueError(
                f"Candidate validation failed: {validation.errors}"
            )

        registry.register(
            candidate.name,
            factor_function,
        )

        specs.append(
            FactorSpec(
                name=candidate.name,
                params=dict(candidate.parameters),
            )
        )

        candidate_info = {
            "name": candidate.name,
            "source_hash": candidate.source_hash,
            "source_path": str(candidate.source_path),
            "parameters": dict(candidate.parameters),
            "hypothesis": candidate.hypothesis,
            "validation": {
                "passed": validation.passed,
                "valid_count": validation.valid_count,
                "total_count": validation.total_count,
            },
        }

    if not specs:
        raise ValueError("At least one factor required")

    features = build_features(
        market=market,
        specs=specs,
        registry=registry,
    )


    feature_columns = features.columns.tolist()
    baseline_columns = feature_columns[:baseline_count]
    candidate_column = feature_columns[-1] if candidate is not None else None


    # --------------------------------------------------------
    # 3. 生成研究数据集
    # --------------------------------------------------------
    dataset = build_research_dataset(
        market=market,
        features=features,
        horizon=config.horizon,
    )

    dataset = dataset.reset_index(drop=True)

    # --------------------------------------------------------
    # 4. 单因子评价
    # --------------------------------------------------------
    factor_reports = {}

    for name in feature_columns:
        print(f"Evaluating factor: {name}")

        report = evaluate_factor(
            factor=dataset[name],
            label=dataset["reference_return"],
            timestamps=dataset["open_time"],
        )

        factor_reports[name] = report

    # --------------------------------------------------------
    # 5. 模型评价：增量评价与普通评价互斥，避免重复训练
    # --------------------------------------------------------
    incremental_result = None
    feedback = None
    model_variants = None

    if candidate is not None and baseline_columns:
        incremental_result, model_variants = evaluate_incremental(
            dataset=dataset,
            candidate_column=candidate_column,
            baseline_columns=baseline_columns,
            test_start=config.test_start,
            test_end=config.test_end,
            train_months=config.train_months,
            retrain_months=config.retrain_months,
            min_train_samples=1000,
            bootstrap_samples=BOOTSTRAP_SAMPLES,
            bootstrap_block_sizes=BOOTSTRAP_BLOCK_SIZES,
            return_model_results=True,
        )
        # 三个模型已经计算完毕，直接复用 combined，不再训练第四次
        model_result = model_variants["combined"]
        feedback = build_research_feedback(
            candidate_name=candidate.name,
            incremental=incremental_result,
        )
    else:
        # 纯内置因子或纯候选因子，沿用原有评价模式
        model_result = evaluate_model(
            dataset=dataset,
            feature_columns=feature_columns,
            model_factory=lambda: LogisticRegression(
                max_iter=1000,
                random_state=42,
            ),
            test_start=config.test_start,
            test_end=config.test_end,
            train_months=config.train_months,
            retrain_months=config.retrain_months,
            min_train_samples=1000,
            scale_features=True,
        )

    # 防止实验运行期间 Agent 修改了候选文件，导致报告身份错误
    if candidate is not None:
        actual_hash = hashlib.sha256(
            candidate.source_path.read_bytes()
        ).hexdigest()
        if actual_hash != candidate.source_hash:
            raise ValueError("Candidate source changed during experiment")

    # --------------------------------------------------------
    # 6. 保存实验产物
    # --------------------------------------------------------
    output_dir = OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    for name, report in factor_reports.items():
        report_dir = output_dir / "factors" / name
        report_dir.mkdir(parents=True, exist_ok=True)

        report["monthly"].to_csv(
            report_dir / "monthly.csv",
            index=False,
        )

        report["yearly"].to_csv(
            report_dir / "yearly.csv",
            index=False,
        )

        report["deciles"].to_csv(
            report_dir / "deciles.csv",
            index=False,
        )

    # 统一 predictions.csv / fold_metrics.csv 始终表示最终选用模型。
    # 增量研究另外保留三组模型的原始预测，供后续审计和配对检验。
    if model_variants is not None:
        for group_name, group_result in model_variants.items():
            group_result.predictions.to_csv(
                output_dir / f"predictions_{group_name}.csv", index=False
            )
            group_result.fold_metrics.to_csv(
                output_dir / f"fold_metrics_{group_name}.csv", index=False
            )

    model_result.predictions.to_csv(
        output_dir / "predictions.csv",
        index=False,
    )

    model_result.fold_metrics.to_csv(
        output_dir / "fold_metrics.csv",
        index=False,
    )

    # --------------------------------------------------------
    # 7. 构建标准实验摘要
    # --------------------------------------------------------
    summary = {
        "run_id": run_id,
        "config_hash": config_hash,
        "status": "completed",
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "horizon": config.horizon,
        "feature_columns": feature_columns,
        "data_rows": len(dataset),
        "factor_evaluation": {
            name: {
                "quality": report["quality"],
                "overall": report["overall"],
                "stability": report["stability"],
            }
            for name, report in factor_reports.items()
        },
        "model_evaluation": model_result.metrics,
        "prediction_rows": len(model_result.predictions),
        "candidate": candidate_info,
        "incremental_evaluation": incremental_result,
        "research_feedback": feedback,
    }

    safe_summary = json_safe(summary)

    # 先写临时文件，再原子替换
    summary_path = output_dir / "summary.json"
    temporary_path = output_dir / "summary.json.tmp"

    serialized = json.dumps(
        safe_summary,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )

    temporary_path.write_text(
        serialized,
        encoding="utf-8",
    )
    temporary_path.replace(summary_path)

    print("\n=== EXPERIMENT SUMMARY ===")
    print(serialized)
    print(f"\nArtifacts: {output_dir}")

    return safe_summary


def run_experiment(config_path: str | Path,
                    candidate_dir: str | Path | None = None,
                    ) -> dict:
    """
    创建实验记录，执行实验，更新最终状态。
    """
    config_path = Path(config_path)

    # 配置不合法时，尚未创建实验记录
    config = ExperimentConfig.from_json(config_path)
    if not config.factors and candidate_dir is None:
        raise ValueError("At least one factor required")
    raw = json.loads(
        config_path.read_text(encoding="utf-8")
    )

    # 实际运行配置包含候选源码身份
    if candidate_dir is not None:
        candidate = submit_candidate(candidate_dir)

        raw["candidate"] = {
            "name": candidate.name,
            "source_hash": candidate.source_hash,
            "parameters": candidate.parameters,
            "prediction_horizon": candidate.prediction_horizon,
        }

        if config.factors:
            raw["incremental_evaluation"] = {
                "baseline_factors": [
                    {"name": spec.name, "params": spec.params}
                    for spec in config.factors
                ],
                "bootstrap_samples": BOOTSTRAP_SAMPLES,
                "bootstrap_block_sizes": list(BOOTSTRAP_BLOCK_SIZES),
            }

        if candidate.prediction_horizon != config.horizon:
            raise ValueError(
                "Candidate prediction_horizon mismatch"
            )
    
    canonical_config = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    config_hash = hashlib.sha256(
        canonical_config.encode("utf-8")
    ).hexdigest()

    registry = ExperimentRegistry(DB_PATH)

    run_id = registry.create_run(
        config_hash=config_hash,
        config_json=canonical_config,
    )

    print(f"Run ID: {run_id}")
    print(f"Config Hash: {config_hash[:16]}")

    try:
        summary = _execute_experiment(
            config=config,
            run_id=run_id,
            config_hash=config_hash,
            candidate_dir=candidate_dir
        )

    except Exception as exc:
        registry.fail_run(
            run_id=run_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    # 仅当计算及文件写入全部成功后，提交完成状态
    registry.complete_run(
        run_id=run_id,
        result_dir=OUTPUT_ROOT / run_id,
    )

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Alpha Lab Experiment Runner"
    )

    parser.add_argument(
        "command",
        choices=["run"],
    )

    parser.add_argument(
        "config",
        help="Path to experiment JSON configuration",
    )
    
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    if args.command == "run":
        run_experiment(args.config,  candidate_dir=args.candidate_dir)


if __name__ == "__main__":
    main()
