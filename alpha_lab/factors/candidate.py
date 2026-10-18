
import hashlib
import json
import re

from dataclasses import dataclass
from pathlib import Path


NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

ALLOWED_COLUMNS = frozenset({
    "open",
    "high",
    "low",
    "close",
    "volume",
})

MAX_SOURCE_BYTES = 64 * 1024


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    hypothesis: str
    parameters: dict
    required_columns: tuple[str, ...]
    prediction_horizon: int
    source_hash: str
    source_path: Path


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_dir: Path
    spec: CandidateSpec
    source_relative_path: str
    manifest_relative_path: str
    manifest_hash: str


def submit_candidate(
    candidate_dir: str | Path,
) -> CandidateSpec:
    """
    验证候选因子的提交结构。

    不导入、不编译、不执行 factor.py。
    """
    directory = Path(candidate_dir).resolve(strict=True)

    if not directory.is_dir():
        raise ValueError("Candidate path must be a directory")

    manifest_path = directory / "candidate.json"
    source_path = directory / "factor.py"

    if not manifest_path.is_file() or not source_path.is_file():
        raise ValueError("Missing candidate.json or factor.py")

    # 防止提交文件通过符号链接指向其他位置
    if manifest_path.is_symlink() or source_path.is_symlink():
        raise ValueError("Symlinks are not permitted")

    if source_path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Factor source too large")

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError("Manifest must be an object")

    required = {
        "name",
        "hypothesis",
        "parameters",
        "required_columns",
        "prediction_horizon",
    }

    if set(raw) != required:
        raise ValueError(
            f"Manifest keys must be exactly {sorted(required)}"
        )

    name = raw["name"]

    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("Invalid candidate name")

    if directory.name != name:
        raise ValueError("Candidate directory must match name")

    hypothesis = raw["hypothesis"]

    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("Hypothesis is required")

    if len(hypothesis) > 4000:
        raise ValueError("Hypothesis too long")

    params = raw["parameters"]

    if not isinstance(params, dict):
        raise ValueError("Parameters must be an object")

    if len(params) > 20:
        raise ValueError("Too many parameters")

    for key, value in params.items():
        if not isinstance(key, str) or not NAME_PATTERN.fullmatch(key):
            raise ValueError("Invalid parameter name")

        if type(value) not in (int, float, str, bool):
            raise ValueError("Invalid parameter type")

        if type(value) is float:
            import math
            if not math.isfinite(value):
                raise ValueError("Non-finite parameter")

    columns = raw["required_columns"]

    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(x, str) for x in columns)
        or not set(columns).issubset(ALLOWED_COLUMNS)
        or len(columns) != len(set(columns))
    ):
        raise ValueError("Invalid required columns")

    horizon = raw["prediction_horizon"]

    if type(horizon) is not int or not 1 <= horizon <= 96:
        raise ValueError("Invalid prediction horizon")

    source_bytes = source_path.read_bytes()

    if not source_bytes:
        raise ValueError("Empty factor source")

    return CandidateSpec(
        name=name,
        hypothesis=hypothesis,
        parameters=dict(params),
        required_columns=tuple(columns),
        prediction_horizon=horizon,
        source_hash=hashlib.sha256(source_bytes).hexdigest(),
        source_path=source_path,
    )


def snapshot_candidate(
    candidate_dir: str | Path,
    run_dir: str | Path,
    *,
    expected: CandidateSpec | None = None,
) -> CandidateSnapshot:
    """Copy one validated candidate into an immutable run-local input tree."""
    source_dir = Path(candidate_dir).resolve(strict=True)
    before = expected or submit_candidate(source_dir)
    current = submit_candidate(source_dir)

    if current != before:
        raise ValueError("Candidate changed before snapshot")

    source_bytes = current.source_path.read_bytes()
    manifest_bytes = (source_dir / "candidate.json").read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != before.source_hash:
        raise ValueError("Candidate source changed before snapshot")

    run_dir = Path(run_dir)
    snapshot_dir = run_dir / "candidate_snapshot" / before.name
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    files = {
        "factor.py": source_bytes,
        "candidate.json": manifest_bytes,
    }
    for name, content in files.items():
        temporary = snapshot_dir / f".{name}.tmp"
        destination = snapshot_dir / name
        temporary.write_bytes(content)
        temporary.replace(destination)

    archived = submit_candidate(snapshot_dir)
    if archived.source_hash != before.source_hash:
        raise ValueError("Candidate snapshot hash mismatch")
    comparable = (
        "name",
        "hypothesis",
        "parameters",
        "required_columns",
        "prediction_horizon",
    )
    if any(getattr(archived, key) != getattr(before, key) for key in comparable):
        raise ValueError("Candidate snapshot manifest mismatch")

    return CandidateSnapshot(
        candidate_dir=snapshot_dir,
        spec=archived,
        source_relative_path=(
            Path("candidate_snapshot") / before.name / "factor.py"
        ).as_posix(),
        manifest_relative_path=(
            Path("candidate_snapshot") / before.name / "candidate.json"
        ).as_posix(),
        manifest_hash=hashlib.sha256(manifest_bytes).hexdigest(),
    )
