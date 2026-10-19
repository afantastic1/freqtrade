"""预注册分析结果的 append-only 记录辅助。

背景：`research_decisions` 与 `statistical_reviews` 都有指向
`experiments(run_id)` 与 `candidates(candidate_id)` 的外键，并且
`ResearchStore.connect()` 打开了 `PRAGMA foreign_keys=ON`。Direction A / B
的配置是**独立分析**（交互模型、非 OHLCV 因子），没有 runner 意义上的
candidate 目录，因此：

- 为每个分析在 `experiments` 里注册一条**独立**的 run（status=completed，
  `result_dir` 指向 notes 目录），作为 `statistical_reviews` 的锚点；
- 每个配置追加一条 `statistical_reviews`（`candidate_id=NULL`，允许）；
- Research Decision 追加到 `notes/prereg_decisions.jsonl`（append-only 文本），
  **不**写入 `research_decisions` 表 —— 因为该表要求一个真实 candidate，
  写入合成 candidate 会污染 candidate 表。

所有写入都是 INSERT，绝不 UPDATE / DELETE 历史行。
"""

from __future__ import annotations

import hashlib
import json
import fcntl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpha_lab.experiment_registry import (
    CompletedRunAvailable,
    ExperimentRegistry,
)
from alpha_lab.research_store import ResearchStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_analysis_run(
    root: str | Path,
    *,
    analysis_id: str,
    config: dict,
    result_dir: str | Path,
    code_version: str,
) -> str:
    """幂等地为一个独立分析注册 experiments 行，返回 run_id。"""
    root = Path(root)
    registry = ExperimentRegistry(root / "experiments.sqlite3")
    config_json = json.dumps(config, sort_keys=True, ensure_ascii=False)
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()

    for row in registry.find_by_config(config_hash):
        if row["status"] == "completed":
            return row["run_id"]

    try:
        run_id = registry.claim_run(
            config_hash=config_hash,
            config_json=config_json,
            code_version=code_version,
            stale_after=timedelta(hours=6),
        )
    except CompletedRunAvailable as exc:  # pragma: no cover - race guard
        return str(exc).rsplit(" ", 1)[-1]

    registry.complete_run(run_id, result_dir=result_dir)
    return run_id


def record_reviews(
    root: str | Path,
    reviews: list[dict],
    *,
    method_version: str,
) -> list[str]:
    """追加 statistical_reviews 行（candidate_id 保持 NULL）。"""
    store = ResearchStore(Path(root) / "experiments.sqlite3")
    ids = []
    for review in reviews:
        ids.append(
            store.record_statistical_review(
                review, method_version=method_version, candidate_id=None
            )
        )
    return ids


def append_decisions(
    root: str | Path,
    decisions: list[dict],
    *,
    log_name: str,
    dedupe_key: str | None = "analysis_id",
) -> Path:
    """把 Research Decision 追加到 append-only JSONL。

    `dedupe_key` 非空时，已存在相同键值的决策会被跳过，保证重复运行幂等
    （JSONL 仍然是只追加、可回放的）。
    """
    path = Path(root) / "notes" / log_name
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        seen = {}
        if dedupe_key:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = existing.get(dedupe_key)
                if key is not None:
                    comparable = {k: v for k, v in existing.items() if k != "recorded_at"}
                    seen[key] = json.dumps(
                        comparable, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False,
                    )
        handle.seek(0, 2)
        for decision in decisions:
            key = decision.get(dedupe_key) if dedupe_key else None
            canonical = json.dumps(
                decision, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
            if key is not None and key in seen:
                if seen[key] == canonical:
                    continue
                raise ValueError(f"conflicting research decision identity: {key}")
            handle.write(
                json.dumps(
                    {"recorded_at": _utc_now(), **decision},
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
            if key is not None:
                seen[key] = canonical
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return path


def read_decisions(root: str | Path, log_name: str) -> list[dict]:
    path = Path(root) / "notes" / log_name
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
