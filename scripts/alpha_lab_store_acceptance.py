"""Provision and exercise the isolated v1.1 cross-sectional store.

The conformance run uses disposable SQLite files and synthetic records only.
It never opens the historical experiments.sqlite3 for writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_lab.cross_sectional_store import CrossSectionalStore
from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore


HISTORIC = ROOT / "user_data/research/alpha_lab/experiments.sqlite3"
STORE_DIR = ROOT / "user_data/research/alpha_lab/stores/v1.1-cross-sectional"
STORE_PATH = STORE_DIR / "experiments.sqlite3"
STORE_ID = "alpha-lab-v1.1-cross-sectional-store"
SCHEMA_VERSION = "1.1.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_not_historic(path: Path) -> None:
    target = path.resolve()
    historic = HISTORIC.resolve()
    if target == historic or (path.exists() and historic.exists() and path.samefile(historic)):
        raise ValueError("refusing to use the historical Alpha Lab database")


def provision(path: Path) -> dict:
    """Create an empty versioned CS store; fail if the target already exists."""
    path = path.resolve()
    _assert_not_historic(path)
    if path.exists():
        raise FileExistsError(f"store already exists; refusing overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    ExperimentRegistry(path)
    CrossSectionalStore(path)
    ResearchStore(path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE alpha_lab_store_identity (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_id TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL,
            database_role TEXT NOT NULL CHECK(database_role='cross-sectional-v1.1'),
            historic_database_sha256 TEXT NOT NULL
        )""")
        db.execute("INSERT INTO alpha_lab_store_identity VALUES (1, ?, ?, ?, ?)",
                   (STORE_ID, SCHEMA_VERSION, "cross-sectional-v1.1", sha256(HISTORIC)))
        db.commit()
    path.chmod(0o600)
    return {"path": str(path), "store_id": STORE_ID,
            "schema_version": SCHEMA_VERSION, "sha256": sha256(path)}


def _exercise(db_path: Path) -> dict:
    registry = ExperimentRegistry(db_path)
    cs = CrossSectionalStore(db_path)
    research = ResearchStore(db_path)
    run_id = registry.create_run(config_hash="synthetic-config", config_json="{}",
                                 code_version="store-acceptance-only",
                                 data_fingerprint="synthetic-data")
    registry.complete_run(run_id, result_dir="synthetic://acceptance")
    payload = {"mode": "cross_sectional", "candidate_source_hash": "a" * 64,
               "results": {"synthetic": {"status": "NOT_EVALUATED"}}}
    evidence_hash = cs.append(run_id, "synthetic-config", payload)
    duplicate_hash = cs.append(run_id, "synthetic-config", payload)
    if duplicate_hash != evidence_hash:
        raise AssertionError("identical evidence append was not idempotent")
    try:
        cs.append(run_id, "synthetic-config", {**payload, "changed": True})
    except ValueError as exc:
        if "conflicting" not in str(exc):
            raise
    else:
        raise AssertionError("conflicting evidence was accepted")

    decision_args = dict(run_id=run_id, candidate_source_hash="a" * 64,
                         supporting_evidence=["synthetic acceptance"],
                         opposing_evidence=[], conclusion="INCONCLUSIVE",
                         next_hypothesis="none", next_actions=[])
    decision = research.create_decision(**decision_args, evidence_id=evidence_hash)
    try:
        research.create_decision(**{**decision_args, "run_id": "missing-run"},
                                  evidence_id=evidence_hash)
    except ValueError as exc:
        if "Unknown run_id" not in str(exc):
            raise
    else:
        raise AssertionError("Decision without Run was accepted")

    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        fks = {table: db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
               for table in ("cs_evaluations", "cs_research_decisions")}
        before = db.execute("SELECT evidence_hash FROM cs_evaluations WHERE run_id=?",
                            (run_id,)).fetchone()[0]

    # SQLite backup creates the disposable mutation-test copy. The original
    # synthetic database and the deployed empty store are never used for writes.
    copy_path = db_path.with_name(db_path.stem + "-mutation-copy.sqlite3")
    with sqlite3.connect(db_path) as source, sqlite3.connect(copy_path) as copy:
        source.backup(copy)
    rejected = {}
    with sqlite3.connect(copy_path) as db:
        for table, rowid_expr in (("cs_evaluations", "run_id=?"),
                                  ("cs_research_decisions", "decision_id=?")):
            key = run_id if table == "cs_evaluations" else decision["decision_id"]
            for operation in ("UPDATE", "DELETE"):
                try:
                    if operation == "UPDATE":
                        db.execute(f"UPDATE {table} SET created_at=created_at WHERE {rowid_expr}", (key,))
                    else:
                        db.execute(f"DELETE FROM {table} WHERE {rowid_expr}", (key,))
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    rejected[f"{table}.{operation.lower()}"] = True
                else:
                    rejected[f"{table}.{operation.lower()}"] = False
    copy_path.unlink()

    # Close/reopen and compare stable identities and JSON payload.
    reopened = CrossSectionalStore(db_path)
    if reopened.evidence_id(run_id) != before:
        raise AssertionError("evidence identity changed after reopen")
    stable_decision = ResearchStore(db_path).list_decisions(run_id)
    if len(stable_decision) != 1 or stable_decision[0]["decision_id"] != decision["decision_id"]:
        raise AssertionError("decision changed after reopen")
    if integrity != "ok" or fk_errors:
        raise AssertionError("SQLite integrity/FK check failed")
    expected = {"cs_evaluations.update", "cs_evaluations.delete",
                "cs_research_decisions.update", "cs_research_decisions.delete"}
    if set(rejected) != expected or not all(rejected.values()):
        raise AssertionError(f"mutation attempts were not all rejected: {rejected}")
    return {"integrity_check": integrity, "foreign_key_errors": len(fk_errors),
            "tables_present": sorted({"cs_evaluations", "cs_research_decisions"} & tables),
            "indexes_present": sorted({"idx_cs_evaluations_config", "idx_cs_decisions_run"} & indexes),
            "foreign_keys": {table: len(rows) for table, rows in fks.items()},
            "duplicate_append_idempotent": True, "conflict_rejected": True,
            "decision_without_run_rejected": True, "reopen_stable": True,
            "actual_mutation_rejections_on_copy": rejected,
            "synthetic_evidence_hash": evidence_hash,
            "synthetic_decision_id": decision["decision_id"]}


def conformance() -> dict:
    with tempfile.TemporaryDirectory(prefix="alpha-lab-store-v11-") as temp:
        result = _exercise(Path(temp) / "synthetic.sqlite3")
    return result


def verify_deployed(path: Path) -> dict:
    path = path.resolve(strict=True)
    _assert_not_historic(path)
    uri = f"file:{path}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        identity = db.execute(
            "SELECT store_id,schema_version,database_role,historic_database_sha256 "
            "FROM alpha_lab_store_identity WHERE singleton=1").fetchone()
        table_names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        table_rows = {}
        fk_counts = {}
        for table in ("cs_evaluations", "cs_research_decisions"):
            table_rows[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            fk_counts[table] = len(db.execute(f"PRAGMA foreign_key_list({table})").fetchall())
    expected = (STORE_ID, SCHEMA_VERSION, "cross-sectional-v1.1", sha256(HISTORIC))
    expected_indexes = {"idx_cs_evaluations_config", "idx_cs_decisions_run"}
    if (integrity != "ok" or fk_errors or identity != expected
            or not {"cs_evaluations", "cs_research_decisions"} <= table_names
            or not expected_indexes <= indexes
            or any(value != 1 for value in fk_counts.values())):
        raise AssertionError("deployed store identity/schema verification failed")
    return {"path": str(path), "identity_valid": True,
            "schema_version": SCHEMA_VERSION, "integrity_check": integrity,
            "foreign_key_errors": len(fk_errors), "foreign_keys_per_cs_table": fk_counts,
            "indexes_present": sorted(expected_indexes & indexes),
            "cs_row_counts": table_rows, "store_sha256": sha256(path),
            "historic_sha256": sha256(HISTORIC)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision", action="store_true",
                        help="create the versioned empty store if absent")
    parser.add_argument("--conformance", action="store_true",
                        help="run synthetic checks in disposable SQLite files")
    parser.add_argument("--verify-deployed", action="store_true",
                        help="verify the deployed store read-only")
    args = parser.parse_args()
    output = {}
    if args.provision:
        output["provisioned"] = provision(STORE_PATH)
    if args.conformance:
        output["conformance"] = conformance()
    if args.verify_deployed:
        output["deployed_store"] = verify_deployed(STORE_PATH)
    if not args.provision and not args.conformance and not args.verify_deployed:
        parser.error("select at least one operation")
    output["historic_database_sha256"] = sha256(HISTORIC)
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
