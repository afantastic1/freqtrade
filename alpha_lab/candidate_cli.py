
import argparse
import json
import tempfile
from pathlib import Path

from alpha_lab.factors.candidate import submit_candidate
from alpha_lab.runner import run_experiment


def main():
    parser = argparse.ArgumentParser(
        description="Alpha Lab candidate CLI"
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    validate_cmd = commands.add_parser("validate")
    validate_cmd.add_argument("candidate_dir", type=Path)

    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("candidate_dir", type=Path)
    run_cmd.add_argument("--timeframe", default="1h")
    run_cmd.add_argument("--start", default="2022-01-01")
    run_cmd.add_argument("--end", default="2025-01-01")
    run_cmd.add_argument("--test-start", default="2023-01-01")
    run_cmd.add_argument("--test-end", default="2025-01-01")

    args = parser.parse_args()

    candidate = submit_candidate(args.candidate_dir)

    if args.command == "validate":
        print(json.dumps({
            "status": "submission_valid",
            "name": candidate.name,
            "source_hash": candidate.source_hash,
            "source_path": str(candidate.source_path),
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "run":
        # 只生成临时配置，不复制行情文件
        config = {
            "symbol": "BTC/USDT:USDT",
            "timeframe": args.timeframe,
            "start": args.start,
            "end": args.end,
            "horizon": candidate.prediction_horizon,
            "factors": [],
            "model": "logistic",
            "train_months": 12,
            "retrain_months": 1,
            "test_start": args.test_start,
            "test_end": args.test_end,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )

            result = run_experiment(
                config_path,
                candidate_dir=args.candidate_dir,
            )

        print(json.dumps({
            "status": "completed",
            "run_id": result["run_id"],
            "candidate": candidate.name,
            "rank_ic": result["factor_evaluation"][
                next(iter(result["factor_evaluation"]))
            ]["overall"]["rank_ic"],
            "model_auc": result["model_evaluation"][
                "model"
            ]["auc"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
