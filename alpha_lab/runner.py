
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from freqtrade.exchange import timeframe_to_seconds

from alpha_lab.config import ExperimentConfig
from alpha_lab.data.loader import load_ohlcv
from alpha_lab.data.research_dataset import build_research_dataset
from alpha_lab.evaluation.factor_report import evaluate_factor
from alpha_lab.factors.builder import build_features
from alpha_lab.factors.registry import default_registry
from alpha_lab.models.evaluator import evaluate_model
from alpha_lab.experiment_registry import CompletedRunAvailable, ExperimentRegistry
from alpha_lab.factors.candidate import snapshot_candidate, submit_candidate
from alpha_lab.factors.candidate_loader import load_candidate
from alpha_lab.factors.registry import FactorSpec
from alpha_lab.validation.factor_validator import FactorValidator
from alpha_lab.evaluation.incremental import evaluate_incremental
from alpha_lab.evaluation.evidence import build_evidence_report
from alpha_lab.research_store import ResearchStore
from alpha_lab.version import ENGINE_VERSION, PROTOCOL_VERSION

DATA_DIR = Path("user_data/data/binance")
OUTPUT_ROOT = Path("user_data/research/alpha_lab/runs")
DB_PATH = Path("user_data/research/alpha_lab/experiments.sqlite3")
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_BLOCK_SIZES = (24, 72, 168)
LABEL_VERSION = "forward_return_binary_v1"
EVALUATION_CODE_PATHS = (
    Path(__file__),
    Path(__file__).parent / "evaluation" / "incremental.py",
    Path(__file__).parent / "evaluation" / "incremental_stability.py",
    Path(__file__).parent / "evaluation" / "factor_report.py",
    Path(__file__).parent / "evaluation" / "evidence.py",
    Path(__file__).parent / "evaluation" / "ic.py",
    Path(__file__).parent / "factors" / "momentum.py",
    Path(__file__).parent / "factors" / "builder.py",
    Path(__file__).parent / "models" / "evaluator.py",
    Path(__file__).parent / "validation" / "walk_forward.py",
    Path(__file__).parent / "labels" / "forward_returns.py",
)


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


def _evaluation_code_version() -> str:
    digest = hashlib.sha256()
    for path in EVALUATION_CODE_PATHS:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _data_source_identity(config: ExperimentConfig) -> dict:
    stem = config.symbol.replace("/", "_").replace(":", "_")
    filename = f"{stem}-{config.timeframe}-futures.feather"
    candidates = (DATA_DIR / "futures" / filename, DATA_DIR / filename)
    path = next((item for item in candidates if item.is_file()), candidates[0])
    identity = {
        "path": str(path.resolve()),
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "start": config.start,
        "end": config.end,
    }
    if path.is_file():
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        identity.update({
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "content_sha256": digest.hexdigest(),
        })
    else:
        identity["missing"] = True
    identity["fingerprint"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identity


def _load_reusable_summary(
    registry: ExperimentRegistry,
    config_hash: str,
    candidate,
) -> tuple[dict | None, tuple[str, ...]]:
    invalid_run_ids = []
    for record in registry.find_by_config(config_hash):
        if record["status"] != "completed":
            continue
        if not record.get("result_dir"):
            invalid_run_ids.append(record["run_id"])
            continue
        result_dir = Path(record["result_dir"])
        summary_path = result_dir / "summary.json"
        manifest_path = result_dir / "manifest.json"
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            archived = manifest["candidate"]
            source_path = result_dir / archived["source_path"]
            candidate_manifest_path = result_dir / archived["manifest_path"]
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            candidate_manifest_hash = hashlib.sha256(
                candidate_manifest_path.read_bytes()
            ).hexdigest()
            archived_spec = submit_candidate(candidate_manifest_path.parent)
            artifacts = manifest["artifacts"]
            if not isinstance(artifacts, dict) or not artifacts:
                raise ValueError("missing artifact hash map")
            actual_artifacts = {
                path.relative_to(result_dir).as_posix()
                for path in result_dir.rglob("*")
                if path.is_file() and path.name != "manifest.json"
            }
            if actual_artifacts != set(artifacts):
                raise ValueError("artifact set changed")
            if any(
                hashlib.sha256((result_dir / relative).read_bytes()).hexdigest() != digest
                for relative, digest in artifacts.items()
            ):
                raise ValueError("artifact hash mismatch")
            if summary.get("prediction_rows") is not None and not {
                "predictions.csv", "fold_metrics.csv"
            } <= set(artifacts):
                raise ValueError("missing required prediction artifacts")
        except (OSError, ValueError, KeyError, TypeError):
            invalid_run_ids.append(record["run_id"])
            continue
        if (
            summary.get("run_id") != record["run_id"]
            or summary.get("config_hash") != config_hash
            or summary.get("status") != "completed"
            or not isinstance(summary.get("candidate"), dict)
            or not isinstance(summary.get("factor_evaluation"), dict)
            or not isinstance(summary.get("model_evaluation"), dict)
            or manifest.get("config_hash") != config_hash
            or archived.get("name") != candidate.name
            or archived.get("source_hash") != candidate.source_hash
            or archived.get("manifest_hash") != candidate_manifest_hash
            or candidate_manifest_hash != hashlib.sha256(
                (candidate.source_path.parent / "candidate.json").read_bytes()
            ).hexdigest()
            or source_hash != candidate.source_hash
            or archived_spec.source_hash != candidate.source_hash
            or archived_spec.parameters != candidate.parameters
            or archived_spec.prediction_horizon != candidate.prediction_horizon
        ):
            invalid_run_ids.append(record["run_id"])
            continue
        summary["reused"] = True
        summary["report_path"] = str(summary_path)
        return summary, tuple(invalid_run_ids)
    return None, tuple(invalid_run_ids)


def _ensure_reused_index(summary: dict) -> dict:
    try:
        ResearchStore(DB_PATH).index_summary(
            summary["run_id"], summary["report_path"]
        )
    except Exception as exc:
        raise RuntimeError(
            f"Reusable artifacts found but indexing failed; run backfill for "
            f"{summary['run_id']}: {exc}"
        ) from exc
    return summary


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
            "required_columns": list(candidate.required_columns),
            "prediction_horizon": candidate.prediction_horizon,
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
        evaluation_mode = "incremental"
        incremental_status = "evaluated"
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
        evaluation_mode = (
            "single_factor" if candidate is not None else "builtin"
        )
        incremental_status = (
            "not_evaluated" if candidate is not None else "not_applicable"
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
    output_dir.mkdir(parents=True, exist_ok=True)

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
    data_range = {
        "start": config.start,
        "end": config.end,
        "test_start": config.test_start,
        "test_end": config.test_end,
    }
    model_metadata = {
        "name": config.model,
        "implementation": "sklearn.linear_model.LogisticRegression",
        "train_months": config.train_months,
        "retrain_months": config.retrain_months,
    }
    baseline_metadata = (
        {
            "source": config.baseline_source,
            "factors": [
                {
                    "name": spec.name,
                    "params": dict(spec.params),
                    "alias": spec.alias,
                    "feature_column": spec.output_name,
                }
                for spec in config.factors
            ],
        }
        if baseline_columns else None
    )
    factor_evaluation = {
        name: {
            "quality": report["quality"],
            "overall": report["overall"],
            "stability": report["stability"],
        }
        for name, report in factor_reports.items()
    }
    evidence_report = build_evidence_report(
        run_id=run_id,
        candidate=candidate_info,
        factor_evaluation=factor_evaluation,
        model_evaluation=model_result.metrics,
        incremental_evaluation=incremental_result,
        evaluation_mode=evaluation_mode,
        incremental_status=incremental_status,
        baseline=baseline_metadata,
        data={
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "range": data_range,
            "rows": len(dataset),
            "label_horizon": config.horizon,
            "label_horizon_seconds": (
                timeframe_to_seconds(config.timeframe) * config.horizon
            ),
            "label_horizon_duration": str(
                pd.Timedelta(
                    seconds=timeframe_to_seconds(config.timeframe) * config.horizon
                )
            ),
        },
        model={
            **model_metadata,
            "test_start": config.test_start,
            "test_end": config.test_end,
            "validation_protocol": "walk_forward",
        },
        bootstrap={
            "samples": BOOTSTRAP_SAMPLES,
            "block_sizes": list(BOOTSTRAP_BLOCK_SIZES),
            "status": (
                "evaluated" if incremental_result is not None else "not_evaluated"
            ),
        },
    )

    summary = {
        "run_id": run_id,
        "config_hash": config_hash,
        "status": "completed",
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "horizon": config.horizon,
        "feature_columns": feature_columns,
        "data_rows": len(dataset),
        "data_range": data_range,
        "model": model_metadata,
        "evaluation_mode": evaluation_mode,
        "incremental_status": incremental_status,
        "baseline": baseline_metadata,
        "factor_evaluation": factor_evaluation,
        "model_evaluation": model_result.metrics,
        "prediction_rows": len(model_result.predictions),
        "candidate": candidate_info,
        "incremental_evaluation": incremental_result,
        "evidence_report": evidence_report,
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
                    force: bool = False,
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

    candidate = None
    data_identity = None
    code_version = None
    # 实际运行配置包含候选源码身份
    if candidate_dir is not None:
        candidate = submit_candidate(candidate_dir)

        raw["candidate"] = {
            "name": candidate.name,
            "source_hash": candidate.source_hash,
            "manifest_hash": hashlib.sha256(
                (candidate.source_path.parent / "candidate.json").read_bytes()
            ).hexdigest(),
            "hypothesis": candidate.hypothesis,
            "parameters": candidate.parameters,
            "prediction_horizon": candidate.prediction_horizon,
            "required_columns": list(candidate.required_columns),
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

        data_identity = _data_source_identity(config)
        code_version = _evaluation_code_version()
        raw["research_identity"] = {
            "engine_version": ENGINE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "data": data_identity,
            "label_version": LABEL_VERSION,
            "evaluation_code_version": code_version,
            "model": {
                "name": config.model,
                "train_months": config.train_months,
                "retrain_months": config.retrain_months,
                "test_start": config.test_start,
                "test_end": config.test_end,
                "min_train_samples": 1000,
                "scale_features": True,
            },
            "bootstrap": {
                "samples": BOOTSTRAP_SAMPLES,
                "block_sizes": list(BOOTSTRAP_BLOCK_SIZES),
            },
        }
    
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

    ignored_completed = ()
    if candidate is not None and not force:
        reusable, ignored_completed = _load_reusable_summary(
            registry, config_hash, candidate
        )
        if reusable is not None:
            return _ensure_reused_index(reusable)

    if candidate is not None:
        while True:
            try:
                run_id = registry.claim_run(
                    config_hash=config_hash,
                    config_json=canonical_config,
                    code_version=code_version,
                    data_fingerprint=data_identity["fingerprint"],
                    ignored_completed_run_ids=ignored_completed,
                    allow_completed=force,
                )
                break
            except CompletedRunAvailable:
                reusable, newly_invalid = _load_reusable_summary(
                    registry, config_hash, candidate
                )
                if reusable is not None:
                    return _ensure_reused_index(reusable)
                ignored_completed = tuple(
                    set(ignored_completed).union(newly_invalid)
                )
    else:
        run_id = registry.create_run(
            config_hash=config_hash,
            config_json=canonical_config,
        )

    print(f"Run ID: {run_id}")
    print(f"Config Hash: {config_hash[:16]}")

    try:
        execution_candidate_dir = candidate_dir
        if candidate_dir is not None:
            output_dir = OUTPUT_ROOT / run_id
            output_dir.mkdir(parents=True, exist_ok=False)
            snapshot = snapshot_candidate(
                candidate_dir,
                output_dir,
                expected=candidate,
            )
            execution_candidate_dir = snapshot.candidate_dir
            manifest = {
                "manifest_version": 1,
                "engine_version": ENGINE_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "run_id": run_id,
                "config_hash": config_hash,
                "config": raw,
                "candidate": {
                    "name": candidate.name,
                    "original_source_path": str(candidate.source_path),
                    "source_path": snapshot.source_relative_path,
                    "manifest_path": snapshot.manifest_relative_path,
                    "source_hash": snapshot.spec.source_hash,
                    "manifest_hash": snapshot.manifest_hash,
                    "required_columns": list(candidate.required_columns),
                    "prediction_horizon": candidate.prediction_horizon,
                },
                "data": data_identity,
                "evaluation_code_version": code_version,
                "label_version": LABEL_VERSION,
            }
            temporary_manifest = output_dir / "manifest.json.tmp"
            temporary_manifest.write_text(
                json.dumps(
                    json_safe(manifest),
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            temporary_manifest.replace(output_dir / "manifest.json")

        summary = _execute_experiment(
            config=config,
            run_id=run_id,
            config_hash=config_hash,
            candidate_dir=execution_candidate_dir,
        )
        if candidate is not None:
            output_dir = OUTPUT_ROOT / run_id
            manifest_path = output_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifacts = {
                path.relative_to(output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output_dir.rglob("*")
                if path.is_file() and path.name != "manifest.json"
            }
            if summary.get("prediction_rows") is not None and not {
                "predictions.csv", "fold_metrics.csv"
            } <= set(artifacts):
                raise ValueError("completed experiment missing prediction artifacts")
            manifest["artifacts"] = artifacts
            temp_manifest = output_dir / "manifest.json.tmp"
            temp_manifest.write_text(
                json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temp_manifest.replace(manifest_path)

    except Exception as exc:
        registry.fail_run(
            run_id=run_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    # 仅当计算及文件写入全部成功后，提交完成状态。
    # 研究索引随后写入；若失败，completed 产物可由 backfill 恢复。
    registry.complete_run(
        run_id=run_id,
        result_dir=OUTPUT_ROOT / run_id,
    )

    if candidate is not None:
        summary_path = OUTPUT_ROOT / run_id / "summary.json"
        try:
            ResearchStore(DB_PATH).index_summary(run_id, summary_path)
        except Exception as exc:
            raise RuntimeError(
                f"Research artifacts completed but indexing failed; "
                f"run backfill for {run_id}: {exc}"
            ) from exc

    summary["reused"] = False
    summary["report_path"] = str(OUTPUT_ROOT / run_id / "summary.json")
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
