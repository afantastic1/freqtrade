import json
import sqlite3

import pandas as pd
import pytest

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.cross_sectional_runner import run_cross_sectional


def test_synthetic_runner_is_idempotent_and_has_separate_sqlite_evidence(tmp_path):
    times = pd.date_range("2024-01-01", periods=32, freq="h", tz="UTC")
    symbols = tuple("ABCDE")
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    values = [100 + (i // 5) * (j + 1) + j for i, j in enumerate([k % 5 for k in range(len(index))])]
    data = pd.DataFrame({"open": values, "close": values, "volume": 100,
                         "available_time": [t + pd.Timedelta(hours=1, seconds=1) for t, _ in index]}, index=index)
    panel = MarketPanel(data)
    config = {"mode": "cross_sectional", "hypothesis": "synthetic relative momentum",
              "symbols": list(symbols), "timeframe": "1h",
              "horizon": 2, "start": times[0].isoformat(),
              "end": (times[-1] + pd.Timedelta(hours=1, seconds=2)).isoformat(),
              "train_end": times[16].isoformat(), "test_end": times[30].isoformat(),
              "universe": {"kind": "fixed", "id": "all", "symbols": list(symbols)},
              "features": [{"name": "relative_momentum", "periods": 2}],
              "n_boot": 49, "block_size": 2}
    first = run_cross_sectional(config, panel, root=tmp_path)
    second = run_cross_sectional(config, panel, root=tmp_path)
    assert first["run_id"] == second["run_id"]
    assert first["mode"] == "cross_sectional"
    assert first["data_fingerprint"] == panel.fingerprint()
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    campaign = CrossSectionalStore(tmp_path / "experiments.sqlite3").family_review()
    assert campaign["family_size"] >= 2
    assert all(row["p_holm"] >= row["p_two_sided"] for row in campaign["entries"])
    with sqlite3.connect(tmp_path / "experiments.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM cs_evaluations").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    from alpha_lab.research_store import ResearchStore
    research = ResearchStore(tmp_path / "experiments.sqlite3")
    decision = research.create_decision(
        run_id=first["run_id"], candidate_source_hash=first["candidate_source_hash"],
        supporting_evidence=["synthetic fixture"], opposing_evidence=[],
        conclusion="INCONCLUSIVE", next_hypothesis="none", next_actions=["ARCHIVE"])
    assert research.list_decisions(first["run_id"])[0]["decision_id"] == decision["decision_id"]
    for conclusion in (
        "SUPPORTED", "SUPPORTED_WITH_CAVEATS", " inconclusive", "inconclusive", {"status": "SUPPORTED"},
        {"verdict": "supported"}, {"decision": "SUPPORTED"},
        {"conclusion": "SUPPORTED"}, {"status": "INCONCLUSIVE", "detail": "SUPPORTED"},
    ):
        with pytest.raises(ValueError, match="closed non-supported vocabulary"):
            research.create_decision(
                run_id=first["run_id"], candidate_source_hash=first["candidate_source_hash"],
                supporting_evidence=["synthetic fixture"], opposing_evidence=[],
                conclusion=conclusion, next_hypothesis="none", next_actions=["ARCHIVE"])
    for conclusion in ("INCONCLUSIVE", "REJECTED", "NOT_EVALUABLE"):
        research.create_decision(
            run_id=first["run_id"], candidate_source_hash=first["candidate_source_hash"],
            supporting_evidence=[], opposing_evidence=[], conclusion=conclusion,
            next_hypothesis="none", next_actions=["ARCHIVE"])
    assert len(research.list_decisions(first["run_id"])) == 4
    summary = tmp_path / "runs" / first["run_id"] / "summary.json"
    assert json.loads(summary.read_text())["run_id"] == first["run_id"]
    (summary.parent / "predictions.csv").unlink()
    replacement = run_cross_sectional(config, panel, root=tmp_path)
    assert replacement["run_id"] != first["run_id"]


def test_changed_panel_content_changes_research_identity(tmp_path):
    times = pd.date_range("2024-01-01", periods=28, freq="h", tz="UTC")
    symbols = tuple("ABCDE")
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    data = pd.DataFrame({"open": [100 + i // 5 + i % 5 for i in range(len(index))],
                         "volume": 100,
                         "available_time": [t + pd.Timedelta(hours=1) for t, _ in index]}, index=index)
    data["close"] = data.open
    config = {"mode": "cross_sectional", "hypothesis": "identity sensitivity",
              "symbols": list(symbols), "timeframe": "1h",
              "horizon": 1, "start": times[0].isoformat(),
              "end": (times[-1] + pd.Timedelta(hours=1, seconds=2)).isoformat(),
              "train_end": times[12].isoformat(), "test_end": times[26].isoformat(),
              "universe": {"kind": "fixed", "id": "all", "symbols": list(symbols)},
              "features": [{"name": "relative_momentum", "periods": 1}],
              "n_boot": 19, "block_size": 1}
    first = run_cross_sectional(config, MarketPanel(data), root=tmp_path)
    changed = data.copy()
    changed["close"] = changed["close"].astype(float)
    changed.loc[(times[5], "A"), "close"] += 0.1
    second = run_cross_sectional(config, MarketPanel(changed), root=tmp_path)
    assert first["run_id"] != second["run_id"]


def test_runner_rejects_panel_available_outside_development_window():
    from alpha_lab.cross_sectional_runner import _validate_config

    times = pd.date_range("2024-01-01", periods=12, freq="h", tz="UTC")
    symbols = tuple("ABCDE")
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    data = pd.DataFrame({
        "open": 1.0, "close": 1.0, "volume": 1.0,
        "available_time": [time + pd.Timedelta(hours=1) for time, _ in index],
    }, index=index)
    panel = MarketPanel(data)
    config = {
        "mode": "cross_sectional", "hypothesis": "availability boundary",
        "symbols": list(symbols), "timeframe": "1h", "horizon": 1,
        "start": times[0].isoformat(), "train_end": times[4].isoformat(),
        "test_end": times[10].isoformat(), "end": (times[-1] + pd.Timedelta(hours=1)).isoformat(),
        "universe": {"kind": "fixed", "id": "all", "symbols": list(symbols)},
        "features": [{"name": "relative_momentum", "periods": 1}],
        "n_boot": 19, "block_size": 2,
    }

    with pytest.raises(ValueError, match="panel availability outside development window"):
        _validate_config(config, panel)


def test_cross_sectional_decision_gate_rejects_missing_mode_evidence(tmp_path):
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    from alpha_lab.experiment_registry import ExperimentRegistry
    from alpha_lab.research_store import ResearchStore

    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="missing-mode", config_json="{}")
    CrossSectionalStore(db).append(run_id, "missing-mode", {"candidate_source_hash": "source"})
    registry.complete_run(run_id, tmp_path / "run")
    with pytest.raises(ValueError, match="evidence mode is missing or invalid"):
        ResearchStore(db).create_decision(
            run_id=run_id, candidate_source_hash="source", supporting_evidence=[],
            opposing_evidence=[], conclusion="INCONCLUSIVE", next_hypothesis="none",
            next_actions=["ARCHIVE"],
        )


def test_legacy_diagnostic_evidence_cannot_be_supported_when_gate_flag_is_enabled(tmp_path, monkeypatch):
    import alpha_lab.research_store as research_store_module
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    from alpha_lab.experiment_registry import ExperimentRegistry
    from alpha_lab.research_store import ResearchStore

    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="diagnostic", config_json="{}")
    evidence = {
        "mode": "cross_sectional",
        "method_version": "cs-time-block-centered-v1",
        "candidate_source_hash": "source",
        "results": {},
    }
    CrossSectionalStore(db).append(run_id, "diagnostic", evidence)
    registry.complete_run(run_id, tmp_path / "run")
    monkeypatch.setattr(research_store_module, "CROSS_SECTIONAL_SUPPORTED_ENABLED", True)

    with pytest.raises(ValueError, match="calibrated evidence qualification"):
        ResearchStore(db).create_decision(
            run_id=run_id, candidate_source_hash="source", supporting_evidence=[],
            opposing_evidence=[], conclusion="SUPPORTED", next_hypothesis="none",
            next_actions=["ARCHIVE"],
        )


def test_supported_requires_approved_method_calibration_and_exact_evidence_id(tmp_path, monkeypatch):
    import alpha_lab.research_store as research_store_module
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    from alpha_lab.experiment_registry import ExperimentRegistry
    from alpha_lab.research_store import ResearchStore

    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="approved-fixture", config_json="{}")
    evidence = {
        "mode": "cross_sectional", "method_version": "fixture-method-v2",
        "calibration_id": "fixture-calibration-v2", "candidate_source_hash": "source",
        "inference_status": "CALIBRATED", "feature_coverage": {"status": "READY"},
        "results": {"primary": {"status": "EVALUATED",
                                  "bootstrap": {"p_two_sided": 0.01}}},
    }
    evidence_id = CrossSectionalStore(db).append(run_id, "approved-fixture", evidence)
    registry.complete_run(run_id, tmp_path / "run")
    monkeypatch.setattr(research_store_module, "CROSS_SECTIONAL_SUPPORTED_ENABLED", True)
    monkeypatch.setattr(
        research_store_module, "CROSS_SECTIONAL_SUPPORTED_ADMISSIONS",
        frozenset({("fixture-method-v2", "fixture-calibration-v2")}),
    )
    store = ResearchStore(db)
    decision = {
        "run_id": run_id, "candidate_source_hash": "source",
        "supporting_evidence": [], "opposing_evidence": [],
        "conclusion": "SUPPORTED", "next_hypothesis": "none",
        "next_actions": ["ARCHIVE"],
    }

    with pytest.raises(ValueError, match="method_version, calibration_id, and evidence_id"):
        store.create_decision(**decision, evidence_id="0" * 64)

    stored = store.create_decision(**decision, evidence_id=evidence_id)
    assert stored["conclusion"] == "SUPPORTED"

    diagnostic = {**evidence, "inference_status": "DIAGNOSTIC_ONLY"}
    diagnostic_run_id = registry.create_run(
        config_hash="diagnostic-status", config_json="{}"
    )
    diagnostic_id = CrossSectionalStore(db).append(
        diagnostic_run_id, "diagnostic-status", diagnostic
    )
    registry.complete_run(diagnostic_run_id, tmp_path / "diagnostic-run")
    diagnostic_run = ResearchStore(db)
    with pytest.raises(ValueError, match="method_version, calibration_id, and evidence_id"):
        diagnostic_run.create_decision(
            **{**decision, "run_id": diagnostic_run_id}, evidence_id=diagnostic_id
        )


def test_cross_sectional_evidence_is_append_only(tmp_path):
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    from alpha_lab.experiment_registry import ExperimentRegistry
    import pytest

    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="key", config_json="{}")
    store = CrossSectionalStore(db)
    original = {"mode": "cross_sectional", "score": 0.2}
    assert store.append(run_id, "key", original) == store.append(run_id, "key", original)
    with pytest.raises(ValueError, match="conflicting"):
        store.append(run_id, "key", {"mode": "cross_sectional", "score": 0.3})
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM cs_evaluations").fetchone()[0] == 1
    assert list(tmp_path.glob("experiments.db.backup-*"))


def test_concurrent_identical_cross_sectional_writes_are_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from alpha_lab.cross_sectional_store import CrossSectionalStore
    from alpha_lab.experiment_registry import ExperimentRegistry

    db = tmp_path / "experiments.db"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="same", config_json="{}")
    store = CrossSectionalStore(db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(lambda _: store.append(run_id, "same", {"score": 1}), range(16)))
    assert len(set(hashes)) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM cs_evaluations").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
