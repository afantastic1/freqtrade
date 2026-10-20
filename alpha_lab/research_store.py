import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alpha_lab.experiment_registry import utc_now


CANDIDATE_MANIFEST_VERSION = 1
CROSS_SECTIONAL_VERDICT_POLICY_VERSION = "cs-verdict-gated-v2"
CROSS_SECTIONAL_SUPPORTED_ENABLED = False
CROSS_SECTIONAL_SUPPORTED_ADMISSIONS: frozenset[tuple[str, str]] = frozenset()
CROSS_SECTIONAL_DECISION_CONCLUSIONS = frozenset({
    "INCONCLUSIVE", "REJECTED", "NOT_EVALUABLE",
})

CANDIDATE_COLUMNS = {
    "hypothesis": "TEXT",
    "source_path": "TEXT",
    "snapshot_path": "TEXT",
    "manifest_json": "TEXT",
    "created_at": "TEXT",
}

EVALUATION_COLUMNS = {
    "candidate_id": "TEXT",
    "rank_ic": "REAL",
    "ic": "REAL",
    "mean_monthly_rank_ic": "REAL",
    "valid_months": "INTEGER",
    "auc": "REAL",
    "candidate_auc": "REAL",
    "baseline_auc": "REAL",
    "combined_auc": "REAL",
    "max_abs_spearman": "REAL",
    "delta_auc": "REAL",
    "delta_logloss": "REAL",
    "delta_brier": "REAL",
    "positive_months": "INTEGER",
    "total_months": "INTEGER",
    "robust_positive": "INTEGER",
    "classification": "TEXT",
    "tags_json": "TEXT",
    "feedback_json": "TEXT",
    "evidence_json": "TEXT",
    "report_path": "TEXT",
    "evaluation_mode": "TEXT",
    "incremental_status": "TEXT",
    "indexed_at": "TEXT",
}

STATISTICAL_REVIEW_COLUMNS = {
    "analysis_id": "TEXT",
    "config_id": "TEXT",
}


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def candidate_id_for(candidate: dict) -> str:
    identity = {
        "name": candidate["name"],
        "source_hash": candidate["source_hash"],
        "parameters": candidate.get("parameters") or {},
        "candidate_manifest_version": CANDIDATE_MANIFEST_VERSION,
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _finite(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class ResearchStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self):
        with self.connect() as source:
            tables = {
                row[0] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "experiments" not in tables:
                raise ValueError("experiments table missing; initialize ExperimentRegistry first")
            expected = {
                "candidates": CANDIDATE_COLUMNS,
                "evaluations": EVALUATION_COLUMNS,
                "statistical_reviews": STATISTICAL_REVIEW_COLUMNS,
                "research_decisions": {},
            }
            expected_triggers = {
                f"{table}_{operation}_immutable"
                for table in (
                    "candidates", "evaluations", "research_decisions",
                    "statistical_reviews",
                )
                for operation in ("update", "delete")
            } | {"evaluations_parent_insert", "evaluations_parent_update"}
            existing_triggers = {
                row[0] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            migration_needed = any(
                table not in tables or bool(
                    set(columns) - {
                        row[1] for row in source.execute(f"PRAGMA table_info({table})")
                    }
                )
                for table, columns in expected.items()
            ) or not expected_triggers.issubset(existing_triggers)
            if migration_needed:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup_path = self.db_path.with_name(
                    f"{self.db_path.name}.backup-{stamp}"
                )
                with sqlite3.connect(backup_path) as backup:
                    source.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("database backup integrity check failed")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='experiments'"
            ).fetchone() is None:
                raise ValueError(
                    "experiments table missing; initialize ExperimentRegistry first"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    hypothesis TEXT,
                    source_path TEXT,
                    snapshot_path TEXT,
                    manifest_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    run_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    rank_ic REAL,
                    ic REAL,
                    mean_monthly_rank_ic REAL,
                    valid_months INTEGER,
                    auc REAL,
                    candidate_auc REAL,
                    baseline_auc REAL,
                    combined_auc REAL,
                    max_abs_spearman REAL,
                    delta_auc REAL,
                    delta_logloss REAL,
                    delta_brier REAL,
                    positive_months INTEGER,
                    total_months INTEGER,
                    robust_positive INTEGER,
                    classification TEXT,
                    tags_json TEXT,
                    feedback_json TEXT,
                    report_path TEXT,
                    evaluation_mode TEXT,
                    incremental_status TEXT,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES experiments(run_id),
                    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_source_hash TEXT NOT NULL,
                    supporting_evidence_json TEXT NOT NULL,
                    opposing_evidence_json TEXT NOT NULL,
                    conclusion_json TEXT NOT NULL,
                    next_hypothesis_json TEXT NOT NULL,
                    next_actions_json TEXT NOT NULL,
                    agent_identity TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES experiments(run_id),
                    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
                )
            """)
            self._add_missing_columns(conn, "candidates", CANDIDATE_COLUMNS)
            self._add_missing_columns(conn, "evaluations", EVALUATION_COLUMNS)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS statistical_reviews (
                    review_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    analysis_id TEXT,
                    config_id TEXT,
                    candidate_id TEXT,
                    method_version TEXT NOT NULL,
                    timeframe TEXT,
                    horizon INTEGER,
                    n INTEGER,
                    n_eff REAL,
                    block_size INTEGER,
                    bootstrap_samples INTEGER,
                    primary_metric TEXT NOT NULL,
                    effect REAL,
                    ci_low REAL,
                    ci_high REAL,
                    mde REAL,
                    adjusted_p REAL,
                    verdict TEXT NOT NULL,
                    mechanism_verdict TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES experiments(run_id)
                )
            """)
            self._add_missing_columns(
                conn, "statistical_reviews", STATISTICAL_REVIEW_COLUMNS
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_statistical_reviews_run "
                "ON statistical_reviews(run_id, method_version)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_statistical_reviews_verdict "
                "ON statistical_reviews(verdict)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_name "
                "ON candidates(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluations_candidate_id "
                "ON evaluations(candidate_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluations_classification "
                "ON evaluations(classification)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiments_finished_at "
                "ON experiments(finished_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_decisions_run "
                "ON research_decisions(run_id, created_at, decision_id)"
            )
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS evaluations_parent_insert
                BEFORE INSERT ON evaluations
                WHEN NOT EXISTS (
                    SELECT 1 FROM experiments WHERE run_id = NEW.run_id
                ) OR NOT EXISTS (
                    SELECT 1 FROM candidates
                    WHERE candidate_id = NEW.candidate_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'evaluation foreign key violation');
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS evaluations_parent_update
                BEFORE UPDATE OF run_id, candidate_id ON evaluations
                WHEN NOT EXISTS (
                    SELECT 1 FROM experiments WHERE run_id = NEW.run_id
                ) OR NOT EXISTS (
                    SELECT 1 FROM candidates
                    WHERE candidate_id = NEW.candidate_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'evaluation foreign key violation');
                END
            """)
            for table in (
                "candidates", "evaluations", "research_decisions",
                "statistical_reviews",
            ):
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_update_immutable
                    BEFORE UPDATE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                """)
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_delete_immutable
                    BEFORE DELETE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                """)

    @staticmethod
    def _add_missing_columns(conn, table: str, definitions: dict):
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, sql_type in definitions.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                )

    @staticmethod
    def _candidate_report(summary: dict, candidate_name: str) -> dict:
        reports = summary.get("factor_evaluation") or {}
        return next(
            (
                value for name, value in reports.items()
                if name == candidate_name
                or name.startswith(candidate_name + "_")
            ),
            {},
        )

    @staticmethod
    def _snapshot_metadata(summary_path: Path, candidate: dict):
        manifest_path = summary_path.parent / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        archived = manifest.get("candidate") or {}
        relative = archived.get("source_path")
        snapshot_path = (
            str(summary_path.parent / relative) if relative else None
        )

        candidate_manifest = {
            "required_columns": candidate.get("required_columns"),
            "prediction_horizon": candidate.get("prediction_horizon"),
            "candidate_manifest_version": CANDIDATE_MANIFEST_VERSION,
        }
        manifest_relative = archived.get("manifest_path")
        if manifest_relative:
            archived_manifest = summary_path.parent / manifest_relative
            if archived_manifest.is_file():
                raw = json.loads(archived_manifest.read_text(encoding="utf-8"))
                candidate_manifest.update({
                    "required_columns": raw.get("required_columns"),
                    "prediction_horizon": raw.get("prediction_horizon"),
                })
        return manifest, snapshot_path, candidate_manifest

    def index_summary(self, run_id: str, summary_path: str | Path) -> dict:
        path = Path(summary_path)
        summary = json.loads(path.read_text(encoding="utf-8"))
        if summary.get("run_id") != run_id:
            raise ValueError("Summary run_id mismatch")
        if summary.get("status") != "completed":
            raise ValueError("Only completed summaries can be indexed")
        candidate = summary.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("Summary has no candidate")

        candidate_id = candidate_id_for(candidate)
        evidence = summary.get("evidence_report")
        if evidence is not None:
            if not isinstance(evidence, dict):
                raise ValueError("evidence_report must be an object")
            if evidence.get("run_id") != run_id:
                raise ValueError("Evidence report run_id mismatch")
            evidence_candidate = evidence.get("candidate")
            if (
                not isinstance(evidence_candidate, dict)
                or evidence_candidate.get("source_hash") != candidate["source_hash"]
            ):
                raise ValueError("Evidence report candidate source_hash mismatch")
        evaluation = evidence if evidence is not None else summary
        report = self._candidate_report(evaluation, candidate["name"])
        overall = report.get("overall") or {}
        factor_stability = report.get("stability") or {}
        incremental = evaluation.get("incremental_evaluation")
        evaluation_context = evaluation.get("evaluation_context") or {}
        legacy_feedback = (
            summary.get("research_feedback") or {}
            if evidence is None else {}
        )
        manifest, snapshot_path, candidate_manifest = self._snapshot_metadata(
            path, candidate
        )

        if incremental:
            mode = evaluation_context.get("evaluation_mode", "incremental")
            incremental_status = evaluation_context.get(
                "incremental_status", "evaluated"
            )
            baseline_auc = _finite((incremental.get("baseline") or {}).get("auc"))
            candidate_auc = _finite((incremental.get("candidate") or {}).get("auc"))
            combined_auc = _finite((incremental.get("combined") or {}).get("auc"))
            gains = incremental.get("incremental") or {}
            similarities = incremental.get("factor_similarity") or {}
            spearman = [
                abs(value) for item in similarities.values()
                if (value := _finite(item.get("spearman"))) is not None
            ]
            stability = incremental.get("stability") or {}
            monthly = stability.get("monthly") or []
            positive_months = sum(
                1 for item in monthly
                if (_finite(item.get("logloss_improvement")) or 0) > 0
            )
            total_months = len(monthly)
            robust_positive = int(bool(stability.get("robust_positive")))
        else:
            mode = evaluation_context.get("evaluation_mode", "single_factor")
            incremental_status = evaluation_context.get(
                "incremental_status", "not_evaluated"
            )
            candidate_auc = _finite(
                ((evaluation.get("model_evaluation") or {}).get("model") or {}).get(
                    "auc"
                )
            )
            baseline_auc = combined_auc = None
            gains = {}
            spearman = []
            positive_months = total_months = robust_positive = None

        values = {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "rank_ic": _finite(overall.get("rank_ic")),
            "ic": _finite(overall.get("ic")),
            "mean_monthly_rank_ic": _finite(
                factor_stability.get("mean_monthly_rank_ic")
            ),
            "valid_months": factor_stability.get("valid_months"),
            "auc": candidate_auc,
            "candidate_auc": candidate_auc,
            "baseline_auc": baseline_auc,
            "combined_auc": combined_auc,
            "max_abs_spearman": max(spearman) if spearman else None,
            "delta_auc": _finite(gains.get("auc")),
            "delta_logloss": _finite(gains.get("logloss")),
            "delta_brier": _finite(gains.get("brier")),
            "positive_months": positive_months,
            "total_months": total_months,
            "robust_positive": robust_positive,
            "classification": legacy_feedback.get("classification") or None,
            "tags_json": (
                _canonical_json(legacy_feedback.get("tags") or [])
                if legacy_feedback else None
            ),
            "feedback_json": (
                _canonical_json(legacy_feedback) if legacy_feedback else None
            ),
            "evidence_json": _canonical_json(evidence) if evidence else None,
            "report_path": str(path),
            "evaluation_mode": mode,
            "incremental_status": incremental_status,
            "indexed_at": utc_now(),
        }

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT status FROM experiments WHERE run_id = ?", (run_id,)
            ).fetchone()
            if parent is None:
                raise ValueError("Missing parent experiment")
            if parent["status"] != "completed":
                raise ValueError("Parent experiment is not completed")
            existing = conn.execute(
                "SELECT candidate_id FROM evaluations WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is not None and existing["candidate_id"] != candidate_id:
                raise ValueError("Run already bound to different candidate_id")
            if existing is not None:
                saved = conn.execute(
                    "SELECT * FROM evaluations WHERE run_id = ?", (run_id,)
                ).fetchone()
                stable_columns = set(values) - {"indexed_at"}
                if any(saved[key] != values[key] for key in stable_columns):
                    raise ValueError("conflicting evaluation evidence for run_id")

            candidate_manifest_json = _canonical_json(candidate_manifest)
            existing_candidate = conn.execute(
                "SELECT manifest_json FROM candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing_candidate is not None and existing_candidate["manifest_json"]:
                try:
                    merged_manifest = json.loads(
                        existing_candidate["manifest_json"]
                    )
                except (TypeError, ValueError):
                    merged_manifest = {}
                for key, value in candidate_manifest.items():
                    if (
                        value is not None and merged_manifest.get(key) is not None
                        and merged_manifest[key] != value
                    ):
                        raise ValueError("conflicting candidate manifest evidence")
                merged_manifest.update({
                    key: value for key, value in candidate_manifest.items()
                    if value is not None
                })
                candidate_manifest_json = _canonical_json(merged_manifest)

            if existing_candidate is not None:
                saved_candidate = conn.execute(
                    "SELECT name, source_hash, parameters_json, hypothesis "
                    "FROM candidates WHERE candidate_id = ?", (candidate_id,)
                ).fetchone()
                if (
                    saved_candidate["name"] != candidate["name"]
                    or saved_candidate["source_hash"] != candidate["source_hash"]
                    or saved_candidate["parameters_json"] != _canonical_json(
                        candidate.get("parameters") or {}
                    )
                ):
                    raise ValueError("conflicting candidate evidence for candidate_id")

            conn.execute("""
                INSERT INTO candidates (
                    candidate_id, name, source_hash, parameters_json,
                    hypothesis, source_path, snapshot_path, manifest_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO NOTHING
            """, (
                candidate_id,
                candidate["name"],
                candidate["source_hash"],
                _canonical_json(candidate.get("parameters") or {}),
                candidate.get("hypothesis"),
                (manifest.get("candidate") or {}).get("original_source_path")
                or candidate.get("source_path"),
                snapshot_path,
                candidate_manifest_json,
                utc_now(),
            ))
            columns = list(values)
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO evaluations ({', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(run_id) DO NOTHING",
                tuple(values[column] for column in columns),
            )

        return self.show_run(run_id)

    @staticmethod
    def _decision_json(value, field: str) -> str:
        try:
            return _canonical_json(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be finite JSON data") from exc

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        for field in (
            "supporting_evidence",
            "opposing_evidence",
            "conclusion",
            "next_hypothesis",
            "next_actions",
        ):
            result[field] = json.loads(result.pop(f"{field}_json"))
        return result

    def create_decision(
        self,
        *,
        run_id: str,
        candidate_source_hash: str,
        supporting_evidence: list | dict,
        opposing_evidence: list | dict,
        conclusion: str | dict,
        next_hypothesis: str | dict,
        next_actions: list | dict,
        agent_identity: str | None = None,
        evidence_id: str | None = None,
    ) -> dict:
        structural_fields = {
            "supporting_evidence": (supporting_evidence, (list, dict)),
            "opposing_evidence": (opposing_evidence, (list, dict)),
            "conclusion": (conclusion, (str, dict)),
            "next_hypothesis": (next_hypothesis, (str, dict)),
            "next_actions": (next_actions, (list, dict)),
        }
        for field, (value, allowed) in structural_fields.items():
            if not isinstance(value, allowed):
                raise TypeError(f"{field} has an invalid structural type")
        if agent_identity is not None and not isinstance(agent_identity, str):
            raise TypeError("agent_identity must be text or None")
        if evidence_id is not None and not isinstance(evidence_id, str):
            raise TypeError("evidence_id must be text or None")

        payloads = {
            "supporting_evidence_json": self._decision_json(
                supporting_evidence, "supporting_evidence"
            ),
            "opposing_evidence_json": self._decision_json(
                opposing_evidence, "opposing_evidence"
            ),
            "conclusion_json": self._decision_json(conclusion, "conclusion"),
            "next_hypothesis_json": self._decision_json(
                next_hypothesis, "next_hypothesis"
            ),
            "next_actions_json": self._decision_json(
                next_actions, "next_actions"
            ),
        }
        decision_id = str(uuid.uuid4())
        created_at = utc_now()

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            identity = conn.execute("""
                SELECT x.status, e.candidate_id, c.source_hash
                FROM experiments x
                LEFT JOIN evaluations e ON e.run_id = x.run_id
                LEFT JOIN candidates c ON c.candidate_id = e.candidate_id
                WHERE x.run_id = ?
            """, (run_id,)).fetchone()
            if identity is None:
                raise ValueError(f"Unknown run_id: {run_id}")
            if identity["status"] != "completed":
                raise ValueError("Research Decision run is not completed")
            if identity["candidate_id"] is None:
                cs_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cs_evaluations'"
                ).fetchone()
                cs_row = (conn.execute("SELECT payload_json,evidence_hash FROM cs_evaluations WHERE run_id=?",
                                       (run_id,)).fetchone() if cs_table else None)
                if cs_row is None:
                    raise ValueError("Research Decision run is not indexed")
                cs_evidence = json.loads(cs_row["payload_json"])
                canonical_evidence = json.dumps(
                    cs_evidence, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                verified_evidence_id = hashlib.sha256(canonical_evidence.encode()).hexdigest()
                if verified_evidence_id != cs_row["evidence_hash"]:
                    raise ValueError("cross-sectional evidence identity is inconsistent")
                if cs_evidence.get("candidate_source_hash") != candidate_source_hash:
                    raise ValueError("candidate source_hash does not match run")
                if cs_evidence.get("mode") != "cross_sectional":
                    raise ValueError("cross-sectional evidence mode is missing or invalid")
                if not CROSS_SECTIONAL_SUPPORTED_ENABLED:
                    if (not isinstance(conclusion, str)
                            or conclusion not in CROSS_SECTIONAL_DECISION_CONCLUSIONS):
                        raise ValueError(
                            "cross-sectional conclusion must use the closed non-supported vocabulary "
                            "while the calibration gate is closed"
                        )
                elif conclusion == "SUPPORTED":
                    method_version = cs_evidence.get("method_version")
                    calibration_id = cs_evidence.get("calibration_id")
                    results = cs_evidence.get("results")
                    has_evaluable_result = isinstance(results, dict) and any(
                        isinstance(result, dict)
                        and result.get("status") == "EVALUATED"
                        and (result.get("bootstrap") or {}).get("p_two_sided") is not None
                        for result in results.values()
                    )
                    if (
                        evidence_id != verified_evidence_id
                        or not isinstance(method_version, str)
                        or not isinstance(calibration_id, str)
                        or (method_version, calibration_id) not in CROSS_SECTIONAL_SUPPORTED_ADMISSIONS
                        or cs_evidence.get("inference_status") != "CALIBRATED"
                        or (cs_evidence.get("feature_coverage") or {}).get("status") != "READY"
                        or not has_evaluable_result
                    ):
                        raise ValueError(
                            "cross-sectional SUPPORTED requires calibrated evidence qualification "
                            "for this method_version, calibration_id, and evidence_id"
                        )
                conn.execute("""
                    INSERT INTO cs_research_decisions (
                        decision_id, run_id, candidate_source_hash,
                        supporting_evidence_json, opposing_evidence_json,
                        conclusion_json, next_hypothesis_json, next_actions_json,
                        agent_identity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (decision_id, run_id, candidate_source_hash,
                      payloads["supporting_evidence_json"], payloads["opposing_evidence_json"],
                      payloads["conclusion_json"], payloads["next_hypothesis_json"],
                      payloads["next_actions_json"], agent_identity, created_at))
                row = conn.execute("SELECT *, NULL AS candidate_id FROM cs_research_decisions WHERE decision_id=?",
                                   (decision_id,)).fetchone()
                return self._decision_from_row(row)
            if identity["source_hash"] != candidate_source_hash:
                raise ValueError("candidate source_hash does not match run")

            conn.execute("""
                INSERT INTO research_decisions (
                    decision_id, run_id, candidate_id, candidate_source_hash,
                    supporting_evidence_json, opposing_evidence_json,
                    conclusion_json, next_hypothesis_json, next_actions_json,
                    agent_identity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_id,
                run_id,
                identity["candidate_id"],
                candidate_source_hash,
                payloads["supporting_evidence_json"],
                payloads["opposing_evidence_json"],
                payloads["conclusion_json"],
                payloads["next_hypothesis_json"],
                payloads["next_actions_json"],
                agent_identity,
                created_at,
            ))
            row = conn.execute(
                "SELECT * FROM research_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return self._decision_from_row(row)

    def list_decisions(self, run_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = list(conn.execute("""
                SELECT * FROM research_decisions
                WHERE run_id = ?
                ORDER BY created_at, decision_id
            """, (run_id,)).fetchall())
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cs_research_decisions'").fetchone():
                rows.extend(conn.execute("SELECT *, NULL AS candidate_id FROM cs_research_decisions WHERE run_id=?",
                                         (run_id,)).fetchall())
        return [self._decision_from_row(row) for row in sorted(rows, key=lambda row: (row["created_at"], row["decision_id"]))]

    def show_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("""
                SELECT x.run_id AS parent_run_id, e.*,
                       x.status, x.started_at, x.finished_at, x.error,
                       c.name AS candidate_name, c.source_hash,
                       c.parameters_json, c.hypothesis, c.source_path,
                       c.snapshot_path, c.manifest_json
                FROM experiments x
                LEFT JOIN evaluations e ON e.run_id = x.run_id
                LEFT JOIN candidates c ON c.candidate_id = e.candidate_id
                WHERE x.run_id = ?
            """, (run_id,)).fetchone()
            cs_row = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cs_evaluations'").fetchone():
                cs_row = conn.execute("SELECT payload_json,evidence_hash FROM cs_evaluations WHERE run_id=?",
                                      (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["run_id"] = result.pop("parent_run_id")
        if cs_row is not None:
            result["mode"] = "cross_sectional"
            result["cross_sectional_evidence"] = json.loads(cs_row["payload_json"])
            result["cross_sectional_evidence_id"] = cs_row["evidence_hash"]
        else:
            result["mode"] = "time_series"
        return result

    def list_runs(
        self,
        *,
        candidate: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        clauses = []
        parameters = []
        if candidate is not None:
            clauses.append("c.name = ?")
            parameters.append(candidate)
        if status is not None:
            clauses.append("x.status = ?")
            parameters.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(limit)
        with self.connect() as conn:
            rows = conn.execute(f"""
                SELECT x.run_id, x.status, x.started_at, x.finished_at, x.error,
                       c.name AS candidate_name, e.classification,
                       e.rank_ic, e.candidate_auc, e.delta_auc,
                       e.evaluation_mode, e.incremental_status, e.report_path
                FROM experiments x
                LEFT JOIN evaluations e ON e.run_id = x.run_id
                LEFT JOIN candidates c ON c.candidate_id = e.candidate_id
                {where}
                ORDER BY x.started_at DESC, x.run_id DESC
                LIMIT ?
            """, parameters).fetchall()
            result = [dict(row) for row in rows]
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cs_evaluations'").fetchone():
                cs_ids = {row[0] for row in conn.execute("SELECT run_id FROM cs_evaluations WHERE run_id IN (" +
                        ",".join("?" for _ in result) + ")", [row["run_id"] for row in result])} if result else set()
            else:
                cs_ids = set()
        for item in result:
            item["mode"] = "cross_sectional" if item["run_id"] in cs_ids else "time_series"
        return result

    # ------------------------------------------------------------------
    # 统计重评（append-only）
    # ------------------------------------------------------------------
    @staticmethod
    def statistical_review_id(
        run_id: str,
        method_version: str,
        n_boot: int,
        random_state: int,
        analysis_id: str | None = None,
        config_id: str | None = None,
    ) -> str:
        key = "|".join(
            str(value) for value in (
                run_id, analysis_id or "", config_id or "",
                method_version, n_boot, random_state,
            )
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def record_statistical_review(
        self,
        review: dict,
        *,
        method_version: str,
        candidate_id: str | None = None,
    ) -> str:
        """追加一条统计重评记录。

        **只 INSERT，不 UPDATE、不 DELETE**：重评是对原始 append-only
        证据的派生结论，历史必须保留。相同 (run_id, method_version, n_boot,
        random_state) 幂等；更换 method_version 会增加新行而不是覆盖旧行。

        `verdict` 列存的是**增量价值轴**的标签（只看 Δ 指标，方向无关），
        `mechanism_verdict` 列存**机制方向轴**的标签。两者按
        `alpha_lab.stats.audit.combine_verdicts` 合并成**最终标签**，
        最终标签在 `payload_json.overall_verdict` 中（`list_statistical_reviews`
        会把它提升为顶层键 `overall_verdict`）。
        """
        run_id = review.get("run_id")
        if not run_id:
            raise ValueError("review requires run_id")
        if not review.get("analysis_id") or not review.get("config_id"):
            raise ValueError("review requires analysis_id and config_id")

        metrics = review.get("metrics") or {}
        primary = review.get("primary_metric", "auc")
        primary_item = metrics.get(primary) or {}

        review_id = self.statistical_review_id(
            run_id,
            method_version,
            int(review.get("bootstrap_samples", 0)),
            int(review.get("random_state", 0)),
            review.get("analysis_id"),
            review.get("config_id"),
        )

        payload_json = json.dumps(
            review, sort_keys=True, separators=(",", ":"), allow_nan=False
        )

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT candidate_id, payload_json FROM statistical_reviews "
                "WHERE review_id = ?", (review_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["candidate_id"] == candidate_id
                    and existing["payload_json"] == payload_json
                ):
                    return review_id
                raise ValueError(
                    f"conflicting statistical review identity: {review_id}"
                )
            conn.execute(
                """
                INSERT INTO statistical_reviews (
                    review_id, run_id, analysis_id, config_id,
                    candidate_id, method_version,
                    timeframe, horizon, n, n_eff, block_size,
                    bootstrap_samples, primary_metric, effect, ci_low,
                    ci_high, mde, adjusted_p, verdict, mechanism_verdict,
                    payload_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    review_id,
                    run_id,
                    review.get("analysis_id"),
                    review.get("config_id"),
                    candidate_id,
                    method_version,
                    review.get("timeframe"),
                    review.get("horizon"),
                    review.get("n"),
                    (review.get("n_eff") or {}).get("design"),
                    review.get("block_size"),
                    review.get("bootstrap_samples"),
                    primary,
                    primary_item.get("point"),
                    primary_item.get("ci_95_lower"),
                    primary_item.get("ci_95_upper"),
                    primary_item.get("mde"),
                    primary_item.get("adjusted_p"),
                    (review.get("verdict") or {}).get("label", "INCONCLUSIVE"),
                    (review.get("mechanism_verdict") or {}).get("label"),
                    payload_json,
                    utc_now(),
                ),
            )
        return review_id

    def list_statistical_reviews(
        self, run_id: str | None = None
    ) -> list[dict]:
        """列出统计重评记录（读侧不修改数据库）。

        返回的每行除表字段外，额外从 `payload_json` 提升两个便于读取的键：

        - `overall_verdict`：**最终标签**（增量轴 + 机制轴合并后的结果）——
          这才是和审计报告摘要一致的那个标签；
        - `overall_reasons`：最终标签的理由列表。

        注意表里的 `verdict` 列是**增量轴**标签，不要直接当成最终结论。
        """
        with self.connect() as conn:
            if run_id is None:
                rows = conn.execute(
                    "SELECT * FROM statistical_reviews "
                    "ORDER BY created_at, review_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM statistical_reviews WHERE run_id = ? "
                    "ORDER BY created_at, review_id",
                    (run_id,),
                ).fetchall()

        reviews = []
        for row in rows:
            item = dict(row)
            try:
                payload = json.loads(item.get("payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}

            overall = payload.get("overall_verdict") or {}
            if isinstance(overall, dict):
                item["overall_verdict"] = overall.get("label")
                item["overall_reasons"] = list(overall.get("reasons") or [])
            else:
                item["overall_verdict"] = overall or None
                item["overall_reasons"] = []

            if not item["overall_verdict"]:
                # 旧记录没有 overall_verdict：退回两轴合并规则
                incremental = item.get("verdict")
                mechanism = item.get("mechanism_verdict")
                if "REJECTED" in (incremental, mechanism):
                    item["overall_verdict"] = "REJECTED"
                elif incremental == "SUPPORTED":
                    item["overall_verdict"] = "SUPPORTED"
                else:
                    item["overall_verdict"] = "INCONCLUSIVE"

            reviews.append(item)

        return reviews
