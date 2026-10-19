"""只读重评逻辑与 append-only 统计记录测试。"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alpha_lab.experiment_registry import ExperimentRegistry
from alpha_lab.research_store import ResearchStore
from alpha_lab.stats.audit import (
    audit_run,
    candidate_factor_key,
    load_incremental_predictions,
    monthly_gain_stability,
    rank_ic_sign_evidence,
)


# --------------------------------------------------------------------------
# 纯函数
# --------------------------------------------------------------------------
def test_candidate_factor_key_excludes_baselines_with_and_without_alias():
    summary = {
        "evidence_report": {
            "evaluation_context": {
                "baseline": {
                    "factors": [
                        {"name": "momentum", "params": {"periods": 4}},
                        {"name": "trend", "params": {"w": 9}, "alias": "trend_9"},
                    ]
                }
            },
            "factor_evaluation": {
                "momentum_periods_4": {},
                "trend_9": {},
                "my_candidate_w_24": {},
            },
        }
    }
    assert candidate_factor_key(summary) == "my_candidate_w_24"


def test_candidate_factor_key_returns_none_when_ambiguous():
    summary = {
        "evidence_report": {
            "evaluation_context": {"baseline": {"factors": []}},
            "factor_evaluation": {"a": {}, "b": {}},
        }
    }
    assert candidate_factor_key(summary) is None


def test_candidate_factor_key_falls_back_to_declared_name_and_params():
    """回填 run 可能同时缺 evidence_report 与 baseline。

    此时不能用"排除基线键"定位候选因子（所有键都像候选），
    必须回退到运行前就固定的 candidate.name + parameters 构造式。
    """
    summary = {
        "evidence_report": {},
        "baseline": None,
        "candidate": {
            "name": "wick_asymmetry_rejection",
            "parameters": {"window": 12},
        },
        "factor_evaluation": {
            "momentum_periods_4": {},
            "momentum_periods_12": {},
            "wick_asymmetry_rejection_window_12": {},
        },
    }

    assert candidate_factor_key(summary) == "wick_asymmetry_rejection_window_12"


def test_candidate_factor_key_fallback_returns_none_when_key_is_absent():
    summary = {
        "evidence_report": {},
        "baseline": None,
        "candidate": {"name": "ghost", "parameters": {"window": 3}},
        "factor_evaluation": {"a": {}, "b": {}},
    }

    assert candidate_factor_key(summary) is None


def test_rank_ic_sign_evidence_binomial_and_fisher_mde():
    strong = rank_ic_sign_evidence(-0.06, 0.888, 36, n_eff=4385)
    assert strong["observed_sign"] == -1
    assert strong["sign_test_p"] < 1e-3
    # Fisher z: se = 1/sqrt(n_eff - 3)
    assert strong["fisher_se"] == pytest.approx(1.0 / np.sqrt(4385 - 3))
    assert strong["mde_rank_ic"] == pytest.approx(2.8015852 * strong["fisher_se"])

    neutral = rank_ic_sign_evidence(0.001, 0.5, 36, n_eff=4385)
    assert neutral["sign_test_p"] > 0.5


def test_monthly_gain_stability_counts_signs_by_month():
    times = pd.to_datetime(
        ["2023-01-01", "2023-01-15", "2023-02-01", "2023-02-15"], utc=True
    )
    gains = np.array([0.1, 0.2, -0.3, -0.5])

    result = monthly_gain_stability(times, gains)
    assert result["months"] == 2
    assert result["positive_months"] == 1
    assert result["positive_ratio"] == pytest.approx(0.5)
    assert result["monthly_sign_test_p"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 端到端：从一个真实形态的最小 run 目录重评
# --------------------------------------------------------------------------
def _write_run(run_dir: Path, run_id: str, *, horizon=4, seed=0):
    run_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    n = 600

    times = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    target = rng.integers(0, 2, n)
    p_base = np.clip(
        0.5 + 0.02 * (target - 0.5) + rng.normal(0, 0.05, n), 0.01, 0.99
    )
    p_comb = np.clip(
        p_base + 0.06 * (target - 0.5) + rng.normal(0, 0.02, n), 0.01, 0.99
    )

    for name, prob in (("baseline", p_base), ("combined", p_comb)):
        pd.DataFrame({
            "open_time": times,
            "target": target,
            "probability": prob,
        }).to_csv(run_dir / f"predictions_{name}.csv", index=False)

    from alpha_lab.stats.metrics import logloss_per_sample

    delta_logloss = float(
        (logloss_per_sample(target, p_base)
         - logloss_per_sample(target, p_comb)).mean()
    )
    from alpha_lab.stats.metrics import fast_auc

    delta_auc = float(fast_auc(target, p_comb) - fast_auc(target, p_base))

    incremental = {
        "factor_similarity": {
            "momentum_periods_4": {"n": n, "pearson": 0.1, "spearman": 0.24}
        },
        "incremental": {"auc": delta_auc, "logloss": delta_logloss, "brier": 0.0},
        "stability": {"robust_positive": False, "bootstrap_sensitivity": {}},
    }

    summary = {
        "run_id": run_id,
        "status": "completed",
        "timeframe": "1h",
        "horizon": horizon,
        "candidate": {"name": "unit_factor", "source_hash": "a" * 64},
        "factor_evaluation": {
            "momentum_periods_4": {
                "overall": {"rank_ic": -0.05},
                "stability": {"valid_months": 20, "negative_rank_ic_ratio": 0.8},
            },
            "unit_factor_w_24": {
                "overall": {"rank_ic": 0.02},
                "stability": {"valid_months": 20, "negative_rank_ic_ratio": 0.4},
            },
        },
        "incremental_evaluation": incremental,
        "evidence_report": {
            "evaluation_context": {
                "baseline": {"factors": [{"name": "momentum", "params": {"periods": 4}}]},
                "data": {"timeframe": "1h", "label_horizon": horizon},
            },
            "factor_evaluation": {
                "momentum_periods_4": {
                    "overall": {"rank_ic": -0.05},
                    "stability": {"valid_months": 20, "negative_rank_ic_ratio": 0.8},
                },
                "unit_factor_w_24": {
                    "overall": {"rank_ic": 0.02},
                    "stability": {"valid_months": 20, "negative_rank_ic_ratio": 0.4},
                },
            },
            "incremental_evaluation": incremental,
        },
    }
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


def test_load_incremental_predictions_rejects_misalignment(tmp_path):
    run_dir = tmp_path / "run"
    _write_run(run_dir, "run-1")
    frame = pd.read_csv(run_dir / "predictions_combined.csv")
    frame.loc[0, "open_time"] = "2024-01-01T00:00:00+00:00"
    frame.to_csv(run_dir / "predictions_combined.csv", index=False)

    with pytest.raises(ValueError):
        load_incremental_predictions(run_dir)


def test_audit_run_reproduces_stored_point_estimates(tmp_path):
    run_dir = tmp_path / "run"
    summary = _write_run(run_dir, "run-1", horizon=4)

    audit = audit_run(run_dir, n_boot=200, robustness_boot=50)

    assert audit["run_id"] == "run-1"
    assert audit["timeframe"] == "1h"
    assert audit["horizon"] == 4
    assert audit["block_size"] == 4
    assert audit["n"] == 600
    assert audit["n_eff"]["design"] == pytest.approx(150.0)

    stored = summary["evidence_report"]["incremental_evaluation"]["incremental"]
    assert audit["stored"]["source"] == "evidence_report"
    assert audit["metrics"]["auc"]["point"] == pytest.approx(
        stored["auc"], abs=1e-12
    )
    assert audit["metrics"]["logloss"]["point"] == pytest.approx(
        stored["logloss"], abs=1e-12
    )

    # 每个指标都有区间、MDE 与实用阈值
    for name in ("auc", "logloss", "brier"):
        item = audit["metrics"][name]
        assert item["ci_95_lower"] <= item["ci_95_upper"]
        assert item["mde"] > 0
        assert item["practical_threshold"] > 0

    # 冗余来自因子相似度（|spearman| = 0.24）
    assert audit["redundancy"]["max_abs_spearman"] == pytest.approx(0.24)
    # 方向：预注册 +1，观测 RankIC 0.02 -> 一致
    assert audit["direction_consistent"] is None  # 未传 preregistered_sign


def test_audit_run_respects_block_below_horizon_skip(tmp_path):
    run_dir = tmp_path / "run"
    _write_run(run_dir, "run-2", horizon=42)
    audit = audit_run(run_dir, n_boot=50, robustness_boot=20)
    assert audit["robustness_blocks"]["24"]["skipped"] == (
        "block_size_below_label_horizon"
    )
    assert audit["block_size"] == 42


# --------------------------------------------------------------------------
# append-only 统计记录
# --------------------------------------------------------------------------
def _make_audit_payload(run_id="run-1"):
    return {
        "run_id": run_id,
        "timeframe": "1h",
        "horizon": 4,
        "n": 600,
        "n_eff": {"design": 150.0},
        "block_size": 4,
        "bootstrap_samples": 200,
        "random_state": 42,
        "primary_metric": "auc",
        "metrics": {
            "auc": {
                "point": 0.01,
                "ci_95_lower": 0.006,
                "ci_95_upper": 0.014,
                "mde": 0.003,
                "adjusted_p": 0.01,
            }
        },
        "verdict": {"label": "SUPPORTED", "reasons": []},
        "mechanism_verdict": {"label": "INCONCLUSIVE", "reasons": []},
    }


def _create_run(db_path: Path, run_id: str) -> None:
    registry = ExperimentRegistry(db_path)
    created = registry.create_run(config_hash="key", config_json="{}")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE experiments SET run_id = ? WHERE run_id = ?",
            (run_id, created),
        )
    registry.complete_run(run_id, db_path.parent / "results")


def test_statistical_review_is_idempotent_and_append_only(tmp_path):
    db = tmp_path / "experiments.db"
    _create_run(db, "run-1")
    store = ResearchStore(db)

    payload = _make_audit_payload()

    first = store.record_statistical_review(payload, method_version="v1")
    second = store.record_statistical_review(payload, method_version="v1")
    assert first == second
    assert len(store.list_statistical_reviews("run-1")) == 1

    rows = store.list_statistical_reviews("run-1")
    assert rows[0]["verdict"] == "SUPPORTED"
    assert rows[0]["effect"] == pytest.approx(0.01)
    assert rows[0]["mde"] == pytest.approx(0.003)

    # 换 method_version -> 追加新行，不覆盖旧行
    third = store.record_statistical_review(payload, method_version="v2")
    assert third != first
    assert len(store.list_statistical_reviews("run-1")) == 2
    assert {row["method_version"] for row in store.list_statistical_reviews()} == {
        "v1",
        "v2",
    }


def test_statistical_review_requires_run_id(tmp_path):
    db = tmp_path / "experiments.db"
    _create_run(db, "run-1")
    store = ResearchStore(db)

    with pytest.raises(ValueError):
        store.record_statistical_review(
            {"metrics": {}}, method_version="v1"
        )


def test_list_statistical_reviews_promotes_overall_verdict(tmp_path):
    """表里的 `verdict` 是增量轴；读侧必须把最终标签提升出来。"""
    db = tmp_path / "experiments.db"
    _create_run(db, "run-1")
    store = ResearchStore(db)

    payload = _make_audit_payload()
    payload["overall_verdict"] = {
        "label": "REJECTED",
        "reasons": ["INCREMENTAL:SUPPORTED", "MECHANISM:REJECTED"],
    }
    store.record_statistical_review(payload, method_version="v1")

    row = store.list_statistical_reviews("run-1")[0]

    # 增量轴仍是 SUPPORTED，但最终标签是 REJECTED
    assert row["verdict"] == "SUPPORTED"
    assert row["mechanism_verdict"] == "INCONCLUSIVE"
    assert row["overall_verdict"] == "REJECTED"
    assert "MECHANISM:REJECTED" in row["overall_reasons"]


def test_list_statistical_reviews_derives_overall_when_absent(tmp_path):
    """缺少 overall_verdict 的旧记录按两轴合并规则回退。"""
    db = tmp_path / "experiments.db"
    _create_run(db, "run-1")
    store = ResearchStore(db)

    payload = _make_audit_payload()  # 无 overall_verdict
    store.record_statistical_review(payload, method_version="v1")

    row = store.list_statistical_reviews("run-1")[0]
    # 增量 SUPPORTED 且机制未被否 -> SUPPORTED
    assert row["overall_verdict"] == "SUPPORTED"

    # 机制 REJECTED -> 最终 REJECTED（任一轴 REJECTED 即 REJECTED）
    payload2 = _make_audit_payload(run_id="run-1")
    payload2["mechanism_verdict"] = {"label": "REJECTED", "reasons": []}
    store.record_statistical_review(payload2, method_version="v2")

    rows = store.list_statistical_reviews("run-1")
    by_version = {row["method_version"]: row for row in rows}
    assert by_version["v2"]["overall_verdict"] == "REJECTED"
