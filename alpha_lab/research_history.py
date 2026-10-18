
import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path("user_data/research/alpha_lab/runs")


def read_history(
    root: Path = DEFAULT_ROOT,
    *,
    limit: int = 20,
    candidate_name: str | None = None,
) -> list[dict]:
    """读取最近的成功实验，提取 Agent 所需的研究反馈。"""
    if limit < 1:
        raise ValueError("limit must be positive")

    if not root.exists():
        return []

    records = []

    for path in root.glob("*/summary.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        if not isinstance(raw, dict):
            continue

        if raw.get("status") != "completed":
            continue

        candidate = raw.get("candidate")
        if not isinstance(candidate, dict):
            continue

        if (
            candidate_name is not None
            and candidate.get("name") != candidate_name
        ):
            continue

        factor_reports = raw.get("factor_evaluation") or {}
        candidate_factor = next(
            (
                item
                for name, item in factor_reports.items()
                if name == candidate.get("name")
                or name.startswith(candidate.get("name", "") + "_")
            ),
            {},
        )

        records.append({
            "run_id": raw.get("run_id"),
            "config_hash": raw.get("config_hash"),
            "candidate": {
                "name": candidate.get("name"),
                "source_hash": candidate.get("source_hash"),
                "hypothesis": candidate.get("hypothesis"),
                "parameters": candidate.get("parameters"),
                "source_path": candidate.get("source_path"),
            },
            "market": {
                "symbol": raw.get("symbol"),
                "timeframe": raw.get("timeframe"),
                "horizon": raw.get("horizon"),
            },
            "factor_overall": candidate_factor.get("overall"),
            "incremental_evaluation": raw.get("incremental_evaluation"),
            "research_feedback": raw.get("research_feedback"),
            "report_path": str(path),
        })

    # 使用文件更新时间排列，仅表示最近写入，不代表实际实验开始时间
    records.sort(
        key=lambda record: (
            Path(record["report_path"]).stat().st_mtime_ns,
            record["run_id"] or "",
        ),
        reverse=True,
    )

    return records[:limit]


def compact_history(records: list[dict]) -> list[dict]:
    """控制 Agent 上下文大小，不传递数千条月度数据。"""
    output = []

    for record in records:
        inc = record["incremental_evaluation"] or {}
        stability = inc.get("stability") or {}

        output.append({
            "run_id": record["run_id"],
            "candidate": record["candidate"],
            "market": record["market"],
            "factor_overall": record["factor_overall"],
            "similarity": inc.get("factor_similarity"),
            "incremental": inc.get("incremental"),
            "bootstrap_robust_positive": stability.get(
                "robust_positive"
            ),
            "feedback": record["research_feedback"],
            "report_path": record["report_path"],
        })

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Query Alpha Lab research history"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--candidate", default=None)
    args = parser.parse_args()

    records = read_history(
        args.root,
        limit=args.limit,
        candidate_name=args.candidate,
    )

    print(json.dumps(
        compact_history(records),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ))


if __name__ == "__main__":
    main()
