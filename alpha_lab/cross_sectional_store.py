"""Append-only evidence table for cross-sectional evaluations."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.stats.multiple_testing import adjust_pvalues


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CrossSectionalStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        ExperimentRegistry(self.db_path)
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            expected_triggers = {
                f"{table}_{operation}_immutable"
                for table in ("cs_evaluations", "cs_research_decisions")
                for operation in ("update", "delete")
            }
            if (
                not {"cs_evaluations", "cs_research_decisions"}.issubset(existing)
                or not expected_triggers.issubset(triggers)
            ):
                backup_path = self.db_path.with_name(self.db_path.name + ".backup-" +
                                                     datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
                with sqlite3.connect(backup_path) as backup:
                    conn.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("SQLite backup failed")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS cs_evaluations (
                run_id TEXT PRIMARY KEY REFERENCES experiments(run_id),
                config_hash TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_evaluations_config ON cs_evaluations(config_hash)")
            conn.execute("""CREATE TABLE IF NOT EXISTS cs_research_decisions (
                decision_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES experiments(run_id),
                candidate_source_hash TEXT NOT NULL,
                supporting_evidence_json TEXT NOT NULL,
                opposing_evidence_json TEXT NOT NULL,
                conclusion_json TEXT NOT NULL,
                next_hypothesis_json TEXT NOT NULL,
                next_actions_json TEXT NOT NULL,
                agent_identity TEXT,
                created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_decisions_run ON cs_research_decisions(run_id, created_at)")
            for table in ("cs_evaluations", "cs_research_decisions"):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_update_immutable
                    BEFORE UPDATE ON {table} BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only'); END""")
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_delete_immutable
                    BEFORE DELETE ON {table} BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only'); END""")

    def append(self, run_id: str, config_hash: str, evidence: dict):
        payload = canonical_json(evidence)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT config_hash,evidence_hash FROM cs_evaluations WHERE run_id=?",
                                    (run_id,)).fetchone()
            if existing is not None:
                if existing != (config_hash, digest):
                    raise ValueError("conflicting cross-sectional evidence")
                return digest
            conn.execute("INSERT INTO cs_evaluations VALUES (?,?,?,?,?)",
                         (run_id, config_hash, digest, payload,
                          datetime.now(timezone.utc).isoformat()))
        return digest

    def family_review(self) -> dict:
        """Read-only Holm/BH correction across distinct development research keys."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            rows = conn.execute("""SELECT c.run_id,c.config_hash,c.payload_json
                FROM cs_evaluations c JOIN experiments x ON x.run_id=c.run_id
                WHERE x.status='completed' ORDER BY c.config_hash,c.created_at,c.run_id""").fetchall()
        unique = {}
        for run_id, config_hash, payload in rows:
            unique.setdefault(config_hash, (run_id, json.loads(payload)))
        entries = []
        for config_hash, (run_id, evidence) in unique.items():
            if evidence.get("family_id") != "2022-2024-usdm-cs-development":
                continue
            for model, result in evidence.get("results", {}).items():
                p = (result.get("bootstrap") or {}).get("p_two_sided")
                if result.get("status") == "EVALUATED" and p is not None:
                    entries.append({"run_id": run_id, "config_hash": config_hash,
                                    "model": model, "p_two_sided": float(p)})
        if entries:
            correction = adjust_pvalues([entry["p_two_sided"] for entry in entries])
            for index, entry in enumerate(entries):
                entry["p_holm"] = correction["holm_bonferroni"][index]
                entry["p_bh"] = correction["benjamini_hochberg"][index]
        return {"family_id": "2022-2024-usdm-cs-development",
                "family_size": len(entries), "entries": entries,
                "status": "diagnostic; no SUPPORTED contract"}
