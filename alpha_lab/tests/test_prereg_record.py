"""预注册记录辅助模块的单元测试（临时目录，不触碰真实数据库）。"""

import json
from pathlib import Path

import pytest

from alpha_lab import prereg_record
from alpha_lab.research_store import ResearchStore


def _review(run_id: str, *, horizon: int, effect: float) -> dict:
    return {
        "run_id": run_id,
        "analysis_id": f"analysis-{horizon}",
        "config_id": f"config-{horizon}",
        "timeframe": "1h",
        "horizon": horizon,
        "n": 1000,
        "n_eff": {"design": 250.0},
        "block_size": horizon,
        "bootstrap_samples": 200,
        "random_state": 42,
        "primary_metric": "auc",
        "metrics": {
            "auc": {
                "metric": "auc",
                "point": effect,
                "ci_95_lower": effect - 0.01,
                "ci_95_upper": effect + 0.01,
                "boot_sd": 0.001,
                "p_two_sided": 0.01,
                "mde": 0.003,
                "practical_threshold": 0.005,
                "adjusted_p": 0.02,
            }
        },
        "verdict": {"label": "INCONCLUSIVE", "reasons": ["EVIDENCE_INCONCLUSIVE"]},
        "mechanism_verdict": {"label": "SUPPORTED", "reasons": ["SIGN"]},
        "overall_verdict": {"label": "INCONCLUSIVE", "reasons": ["x"]},
    }


def test_ensure_analysis_run_is_idempotent(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    config = {"analysis": "unit-test", "setting": "a"}

    first = prereg_record.ensure_analysis_run(
        tmp_path,
        analysis_id="unit-a",
        config=config,
        result_dir=tmp_path / "notes",
        code_version="v1",
    )
    second = prereg_record.ensure_analysis_run(
        tmp_path,
        analysis_id="unit-a",
        config=config,
        result_dir=tmp_path / "notes",
        code_version="v1",
    )
    assert first == second

    # 不同 config -> 不同 run
    third = prereg_record.ensure_analysis_run(
        tmp_path,
        analysis_id="unit-b",
        config={"analysis": "unit-test", "setting": "b"},
        result_dir=tmp_path / "notes",
        code_version="v1",
    )
    assert third != first


def test_record_reviews_keeps_candidate_null(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    run_id = prereg_record.ensure_analysis_run(
        tmp_path,
        analysis_id="unit-a",
        config={"analysis": "unit-test"},
        result_dir=tmp_path / "notes",
        code_version="v1",
    )
    ids = prereg_record.record_reviews(
        tmp_path,
        [_review(run_id, horizon=4, effect=0.01)],
        method_version="unit-v1",
    )
    assert len(ids) == 1

    store = ResearchStore(tmp_path / "experiments.sqlite3")
    rows = [r for r in store.list_statistical_reviews()
            if r["method_version"] == "unit-v1"]
    assert len(rows) == 1
    assert rows[0]["candidate_id"] is None
    assert rows[0]["verdict"] == "INCONCLUSIVE"
    assert rows[0]["overall_verdict"] == "INCONCLUSIVE"

    # 完全相同证据 -> 幂等，不重复插入
    ids_again = prereg_record.record_reviews(
        tmp_path,
        [_review(run_id, horizon=4, effect=0.01)],
        method_version="unit-v1",
    )
    assert ids_again == ids
    rows = [r for r in store.list_statistical_reviews()
            if r["method_version"] == "unit-v1"]
    assert len(rows) == 1

    # 同一身份对应不同证据必须显式冲突，原证据保持不变。
    with pytest.raises(ValueError, match="conflicting statistical review"):
        prereg_record.record_reviews(
            tmp_path,
            [_review(run_id, horizon=4, effect=0.02)],
            method_version="unit-v1",
        )
    assert store.list_statistical_reviews(run_id)[0]["effect"] == 0.01


def test_review_identity_includes_analysis_and_config(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    run_id = prereg_record.ensure_analysis_run(
        tmp_path, analysis_id="shared", config={"x": 1},
        result_dir=tmp_path / "notes", code_version="v1",
    )
    first = _review(run_id, horizon=4, effect=0.01)
    second = _review(run_id, horizon=8, effect=0.02)
    ids = prereg_record.record_reviews(
        tmp_path, [first, second], method_version="unit-v2"
    )
    assert len(set(ids)) == 2
    rows = ResearchStore(tmp_path / "experiments.sqlite3").list_statistical_reviews(run_id)
    assert {(r["analysis_id"], r["config_id"]) for r in rows} == {
        ("analysis-4", "config-4"), ("analysis-8", "config-8")
    }


def test_append_decisions_is_append_only_and_idempotent(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    decisions = [
        {"analysis_id": "a", "run_id": "r1", "conclusion": {"v": 1}},
        {"analysis_id": "b", "run_id": "r2", "conclusion": {"v": 2}},
    ]
    path = prereg_record.append_decisions(
        tmp_path, decisions, log_name="unit.jsonl"
    )
    assert path.is_file()
    assert len(prereg_record.read_decisions(tmp_path, "unit.jsonl")) == 2

    # 重复调用不追加
    prereg_record.append_decisions(tmp_path, decisions, log_name="unit.jsonl")
    assert len(prereg_record.read_decisions(tmp_path, "unit.jsonl")) == 2

    # 新决策会追加
    prereg_record.append_decisions(
        tmp_path,
        [{"analysis_id": "c", "run_id": "r3", "conclusion": {"v": 3}}],
        log_name="unit.jsonl",
    )
    rows = prereg_record.read_decisions(tmp_path, "unit.jsonl")
    assert len(rows) == 3
    assert all("recorded_at" in row for row in rows)


def test_append_decisions_reads_back_valid_json(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    prereg_record.append_decisions(
        tmp_path,
        [{"analysis_id": "a", "conclusion": {"nested": [1, 2, 3]}}],
        log_name="unit.jsonl",
    )
    raw = (tmp_path / "notes" / "unit.jsonl").read_text(encoding="utf-8")
    parsed = json.loads(raw.splitlines()[0])
    assert parsed["conclusion"]["nested"] == [1, 2, 3]


def test_append_decisions_rejects_same_identity_with_changed_evidence(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    prereg_record.append_decisions(
        tmp_path, [{"analysis_id": "a", "conclusion": {"v": 1}}],
        log_name="unit.jsonl",
    )
    with pytest.raises(ValueError, match="conflicting research decision"):
        prereg_record.append_decisions(
            tmp_path, [{"analysis_id": "a", "conclusion": {"v": 2}}],
            log_name="unit.jsonl",
        )
