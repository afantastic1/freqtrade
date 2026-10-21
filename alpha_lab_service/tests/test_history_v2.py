import hashlib

import pytest

from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore
from alpha_lab_service.history_v2 import UnifiedResearchHistory
from alpha_lab_service.store import ResearchStoreV1
from alpha_lab_service.store_v2 import ResearchStoreV2


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stores(tmp_path):
    legacy_path = tmp_path / "legacy" / "experiments.sqlite3"
    registry = ExperimentRegistry(legacy_path)
    registry.create_run(config_hash="legacy", config_json="{}", run_id="legacy-run")
    legacy = ResearchStore(legacy_path)
    v1 = ResearchStoreV1(tmp_path / "v1" / "experiments.sqlite3", protected_paths=())
    v1.append_task(task_id="v1-task", request_hash="1" * 64, request={"timeframe": "5m"})
    v1.append_evidence(task_id="v1-task", run_id="1" * 64, evidence={"status": "DIAGNOSTIC", "report": {"metrics": {}}})
    v2 = ResearchStoreV2(tmp_path / "v2" / "experiments.sqlite3", protected_paths=())
    v2.create_or_get_task(task_id="v2-task", request_hash="2" * 64,
                          request={"factor_request": {"source": "private"}},
                          plan_hash="3" * 64,
                          chunks=[{"chunk_id": "chunk-0", "chunk_index": 0, "input_hash": "4" * 64}])
    return legacy, v1, v2


def test_unified_history_merges_sources_with_store_provenance(tmp_path):
    legacy, v1, v2 = _stores(tmp_path)
    result = UnifiedResearchHistory({"legacy": legacy, "python-factor-v1": v1, "python-factor-v2": v2}).list_history()
    assert {entry["store_id"] for entry in result["entries"]} == {
        "legacy-research", "alpha-lab-python-factor-store-v1", "alpha-lab-python-factor-store-v2"
    }
    assert set(result["source_status"].values()) == {"AVAILABLE"}
    assert all("protocol_version" in entry for entry in result["entries"])


def test_history_reports_unavailable_configured_source_without_silent_drop(tmp_path):
    _legacy, v1, v2 = _stores(tmp_path)

    class BrokenStore:
        def list_runs(self, limit=100):
            raise OSError("store unavailable")

    result = UnifiedResearchHistory({"legacy": BrokenStore(), "python-factor-v1": v1, "python-factor-v2": v2}).list_history()
    assert result["source_status"]["legacy"] == "UNAVAILABLE"
    assert result["source_errors"]["legacy"] == "OSError"
    assert {entry["store_id"] for entry in result["entries"]} >= {"alpha-lab-python-factor-store-v1"}


def test_history_limit_is_bounded(tmp_path):
    legacy, v1, v2 = _stores(tmp_path)
    facade = UnifiedResearchHistory({"legacy": legacy, "python-factor-v1": v1, "python-factor-v2": v2})
    with pytest.raises(ValueError, match="limit"):
        facade.list_history(limit=101)
    with pytest.raises(ValueError, match="limit"):
        facade.list_history(limit=0)


def test_history_query_does_not_mutate_store_bytes(tmp_path):
    legacy, v1, v2 = _stores(tmp_path)
    before = (_sha(legacy.db_path), _sha(v1.path), _sha(v2.path))
    UnifiedResearchHistory({"legacy": legacy, "python-factor-v1": v1, "python-factor-v2": v2}).list_history()
    assert (_sha(legacy.db_path), _sha(v1.path), _sha(v2.path)) == before
