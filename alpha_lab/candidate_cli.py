
import argparse
import json
import tempfile
from pathlib import Path

from alpha_lab.config import default_baseline_factors
from alpha_lab.factors.candidate import submit_candidate
from alpha_lab.runner import run_experiment


def _factor_json(spec):
    value = {
        "name": spec.name,
        "params": dict(spec.params),
    }
    if spec.alias is not None:
        value["alias"] = spec.alias
    return value


def _candidate_factor_report(result: dict, candidate_name: str) -> dict:
    reports = result.get("factor_evaluation") or {}
    return next(
        (
            report for name, report in reports.items()
            if name == candidate_name
            or name.startswith(candidate_name + "_")
        ),
        {},
    )


def main(argv=None):
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
    baseline_group = run_cmd.add_mutually_exclusive_group()
    baseline_group.add_argument(
        "--baseline-config",
        type=Path,
        help="Use the complete experiment config and its explicit factors.",
    )
    baseline_group.add_argument(
        "--no-baseline",
        action="store_true",
        help="Run the candidate alone without incremental evaluation.",
    )
    run_cmd.add_argument(
        "--force",
        action="store_true",
        help="Run again even when an identical completed study exists.",
    )

    args = parser.parse_args(argv)

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
        if args.baseline_config is not None:
            config = json.loads(
                args.baseline_config.read_text(encoding="utf-8")
            )
            config.setdefault("baseline_source", "baseline_config")
        else:
            factors = (
                () if args.no_baseline else default_baseline_factors()
            )
            config = {
                "symbol": "BTC/USDT:USDT",
                "timeframe": args.timeframe,
                "start": args.start,
                "end": args.end,
                "horizon": candidate.prediction_horizon,
                "factors": [_factor_json(item) for item in factors],
                "model": "logistic",
                "train_months": 12,
                "retrain_months": 1,
                "test_start": args.test_start,
                "test_end": args.test_end,
                "baseline_source": (
                    "disabled" if args.no_baseline
                    else "candidate_cli_default"
                ),
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
                force=args.force,
            )

        candidate_report = _candidate_factor_report(
            result, candidate.name
        )
        incremental = result.get("incremental_evaluation") or {}
        delta = incremental.get("incremental") or {}
        print(json.dumps({
            "status": "completed",
            "run_id": result["run_id"],
            "reused": bool(result.get("reused", False)),
            "candidate": candidate.name,
            "evaluation_mode": result.get(
                "evaluation_mode",
                "incremental" if incremental else "single_factor",
            ),
            "incremental_status": result.get(
                "incremental_status",
                "evaluated" if incremental else "not_evaluated",
            ),
            "rank_ic": (
                candidate_report.get("overall") or {}
            ).get("rank_ic"),
            "model_auc": (
                result.get("model_evaluation", {}).get("model") or {}
            ).get("auc"),
            "delta_auc": delta.get("auc"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
