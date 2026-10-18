from __future__ import annotations

import importlib.util
import sys
import sqlite3
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def conn(tmp_path):
    from registry import connect, migrate

    c = connect(tmp_path / "alpha.sqlite")
    migrate(c)
    yield c
    c.close()


def _hyp(hid="H-T1", **over):
    base = dict(id=hid, thesis="t", mechanism="m", expected_sign=1, horizon="24h",
                falsification="f", semantic_family="fam")
    base.update(over)
    return base


def _factor(fid="F-T1", hid="H-T1", **over):
    base = dict(id=fid, hypothesis_id=hid, name="n", formula_ast={"op": "returns"},
                semantic_family="fam", inputs=["close"], lookback=24, frequency="1h",
                availability_rule="bar_close_ts <= decision_ts", direction=1,
                parameters={"periods": 24})
    base.update(over)
    return base


def test_dataset_register_is_idempotent_and_hashes(conn, tmp_path):
    import registry_ops as OPS

    f = tmp_path / "d.bin"
    f.write_bytes(b"hello")
    r1 = OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY",
                              path="x", source_version="v1", schema={"a": 1},
                              hash_paths=[str(f)])
    r2 = OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY",
                              path="x", source_version="v1", schema={"a": 1},
                              hash_paths=[str(f)])
    assert r1["status"] == "inserted"
    assert r2["status"] == "already_exists"
    assert conn.execute("SELECT count(*) FROM datasets").fetchone()[0] == 1


def test_factor_duplicate_instance_hash_is_rejected_not_duplicated(conn):
    import registry_ops as OPS

    OPS.add_hypothesis(conn, _hyp())
    OPS.add_factor(conn, _factor())
    dup = OPS.add_factor(conn, _factor(fid="F-T1b"))  # same formula+parameters
    assert dup["status"] == "already_exists"
    assert dup["id"] == "F-T1"
    assert conn.execute("SELECT count(*) FROM factors").fetchone()[0] == 1


def test_locked_dataset_experiment_requires_human_approval(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-LOCK", access_class="LOCKED", path="2026",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp())
    OPS.add_factor(conn, _factor())
    exp = dict(id="E-T1", factor_id="F-T1", dataset_id="DS-LOCK", split_role="EXPLORATORY",
               target_spec={"h": 24}, test_family_id="TF1")
    with pytest.raises(PermissionError, match="requires explicit human approval"):
        OPS.add_experiment(conn, exp)
    # after an explicit human approval the experiment can be registered (append-only)
    OPS.record_exposure(conn, dataset_id="DS-LOCK", purpose="independent snapshot",
                        approved_by="human:von")
    ok = OPS.add_experiment(conn, exp)
    assert ok["status"] == "inserted"


def test_evaluation_one_per_experiment_and_append_only(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp())
    OPS.add_factor(conn, _factor())
    OPS.add_experiment(conn, dict(id="E-T1", factor_id="F-T1", dataset_id="DS-EX",
                                  split_role="EXPLORATORY", target_spec={"h": 24},
                                  test_family_id="TF1"))
    ev = dict(experiment_id="E-T1", horizon="24h", metrics={"ic": 0.01},
              gate_decision="INSUFFICIENT", evidence_level="EXPLORATORY")
    assert OPS.add_evaluation(conn, ev)["status"] == "inserted"
    assert OPS.add_evaluation(conn, ev)["status"] == "already_exists"
    # 修正不覆盖历史：新建一个 experiment revision，其评价用 supersedes_id 指向旧评价
    OPS.add_experiment(conn, dict(id="E-T1-rev", factor_id="F-T1", dataset_id="DS-EX",
                                  split_role="EXPLORATORY", target_spec={"h": 24, "rev": 2},
                                  test_family_id="TF1"))
    ev2 = dict(ev, experiment_id="E-T1-rev", supersedes_id=1, gate_decision="PROMISING")
    assert OPS.add_evaluation(conn, ev2)["status"] == "inserted"
    rows = OPS.registry_show(conn, "evaluations")
    assert len(rows) == 2


def test_evaluation_accepts_p6_low_power_verdict_and_rejects_unknown(conn):
    """P6 低功效窗口的三档判定必须可落库：INCONCLUSIVE_LOW_POWER 不应被当成非法值。"""
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp())
    OPS.add_factor(conn, _factor())
    OPS.add_experiment(conn, dict(id="E-T1", factor_id="F-T1", dataset_id="DS-EX",
                                  split_role="EXPLORATORY", target_spec={"h": 24},
                                  test_family_id="TF1"))
    ok = OPS.add_evaluation(conn, dict(experiment_id="E-T1", horizon="24h",
                                       metrics={"ic": 0.01},
                                       gate_decision="INCONCLUSIVE_LOW_POWER",
                                       evidence_level="INDEPENDENT_OOS"))
    assert ok["status"] == "inserted"
    with pytest.raises(ValueError, match="gate_decision must be one of"):
        OPS.add_evaluation(conn, dict(experiment_id="E-T1", horizon="24h", metrics={},
                                      gate_decision="NOT_A_VERDICT",
                                      evidence_level="EXPLORATORY"))


def test_evaluation_rejects_unknown_supersedes(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp())
    OPS.add_factor(conn, _factor())
    OPS.add_experiment(conn, dict(id="E-T1", factor_id="F-T1", dataset_id="DS-EX",
                                  split_role="EXPLORATORY", target_spec={"h": 24},
                                  test_family_id="TF1"))
    with pytest.raises(ValueError, match="unknown supersedes_id"):
        OPS.add_evaluation(conn, dict(experiment_id="E-T1", horizon="24h", metrics={},
                                      gate_decision="PROMISING", evidence_level="EXPLORATORY",
                                      supersedes_id=999))


def test_run_lifecycle_and_audit_trail(conn):
    import registry_ops as OPS

    OPS.start_run(conn, dict(id="R-T1", purpose="unit", proposal_budget=1, eval_budget=2))
    OPS.start_run(conn, dict(id="R-T1", purpose="unit"))
    assert conn.execute("SELECT count(*) FROM research_runs").fetchone()[0] == 1
    OPS.record_selection(conn, dict(run_id="R-T1", decision="EXPLORE", rationale="r"))
    OPS.finish_run(conn, run_id="R-T1", status="DONE", decision={"verdict": "NO-GO"},
                   used_budget=1)
    assert OPS.finish_run(conn, run_id="R-T1", status="DONE", decision={})["status"] == "already_finished"
    assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 3


def test_registry_rejects_unknown_table(conn):
    import registry_ops as OPS

    with pytest.raises(ValueError, match="unknown table"):
        OPS.registry_show(conn, "drop_table")


def test_import_bundle_orders_dependencies_and_is_idempotent(conn):
    import registry_ops as OPS

    bundle = {
        "runs": [dict(id="R-B", purpose="bundle")],
        "datasets": [dict(dataset_id="DS-B", access_class="EXPLORATORY", path="p",
                          source_version="v1", schema={})],
        "hypotheses": [_hyp("H-B")],
        "factors": [_factor("F-B", "H-B")],
        "experiments": [dict(id="E-B", factor_id="F-B", dataset_id="DS-B",
                             split_role="EXPLORATORY", target_spec={"h": 24},
                             test_family_id="TF-B")],
        "evaluations": [dict(experiment_id="E-B", horizon="24h", metrics={"ic": 0.0},
                             gate_decision="INSUFFICIENT", evidence_level="EXPLORATORY")],
    }
    r1 = OPS.import_bundle(conn, bundle)
    r2 = OPS.import_bundle(conn, bundle)
    assert r1["counts"]["factors"]["inserted"] == 1
    assert r2["counts"]["factors"]["already_exists"] == 1
    assert conn.execute("SELECT count(*) FROM experiments").fetchone()[0] == 1


def test_import_bundle_writes_tradability_after_experiment(conn):
    """短周期 bundle 走同一条受控写入路径落 tradability（快照，可重复追加）。"""
    import registry_ops as OPS

    bundle = {
        "datasets": [dict(dataset_id="DS-SC", access_class="EXPLORATORY", path="p",
                          source_version="v1", schema={})],
        "hypotheses": [_hyp("H-SC")],
        "factors": [_factor("F-SC", "H-SC", timeframe="15m")],
        "experiments": [_short_exp("E-SC", "F-SC", "DS-SC")],
        "tradability": [dict(experiment_id="E-SC", timeframe="15m", horizon_duration="1h",
                             capital_usdt=300, verdict="PASS",
                             checks={"capital_ok": True})],
    }
    r1 = OPS.import_bundle(conn, bundle)
    assert r1["counts"]["tradability"]["inserted"] == 1
    assert conn.execute("SELECT verdict FROM tradability").fetchone()[0] == "PASS"
    # 二次导入：实验幂等不重复，可交易性快照按 append-only 追加一条
    r2 = OPS.import_bundle(conn, bundle)
    assert r2["counts"]["experiments"]["already_exists"] == 1
    assert conn.execute("SELECT count(*) FROM tradability").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 短周期（schema 002）：K 线周期 / 预测窗口 / 可交易性
# ---------------------------------------------------------------------------
def _short_exp(eid="E-SC", fid="F-SC", did="DS-EX", **over):
    base = dict(id=eid, factor_id=fid, dataset_id=did, split_role="EXPLORATORY",
                target_spec={"timeframe": "15m", "horizon": "1h"}, test_family_id="TF-SC")
    base.update(over)
    return base


def test_factor_accepts_short_cycle_timeframe_and_rejects_unknown(conn):
    import registry_ops as OPS

    OPS.add_hypothesis(conn, _hyp("H-SC"))
    ok = OPS.add_factor(conn, _factor("F-SC", "H-SC", timeframe="15m"))
    assert ok["status"] == "inserted"
    assert conn.execute("SELECT timeframe FROM factors WHERE id = 'F-SC'").fetchone()[0] == "15m"
    with pytest.raises(ValueError, match="timeframe must be one of"):
        OPS.add_factor(conn, _factor("F-BAD", "H-SC", timeframe="7m",
                                     formula_ast={"op": "returns", "n": 7}))


def test_experiment_records_horizon_and_capital_and_rejects_bad_values(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp("H-SC"))
    OPS.add_factor(conn, _factor("F-SC", "H-SC", timeframe="15m"))
    ok = OPS.add_experiment(conn, _short_exp(timeframe="15m", horizon_duration="1h",
                                             capital_usdt=300))
    assert ok["status"] == "inserted"
    row = conn.execute("SELECT timeframe, horizon_duration, capital_usdt FROM experiments "
                       "WHERE id = 'E-SC'").fetchone()
    assert (row["timeframe"], row["horizon_duration"], row["capital_usdt"]) == ("15m", "1h", 300.0)
    with pytest.raises(ValueError, match="horizon_duration must be one of"):
        OPS.add_experiment(conn, _short_exp("E-BADH", factor_id="F-SC",
                                            target_spec={"timeframe": "15m", "horizon": "3h"},
                                            horizon_duration="3h"))
    with pytest.raises(ValueError, match="capital_usdt must be > 0"):
        OPS.add_experiment(conn, _short_exp("E-BADC", factor_id="F-SC",
                                            target_spec={"timeframe": "15m", "horizon": "2h"},
                                            capital_usdt=0))


def test_experiment_accepts_hourly_horizons_used_by_p5_p6(conn):
    """P5/P6 的正式预测窗口是 6h/12h/24h/48h，必须能进**类型化**的 horizon_duration，
    否则「冻结预测窗口」只能写在自由文本里、无法被注册表校验。"""
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp("H-SC"))
    OPS.add_factor(conn, _factor("F-SC", "H-SC", timeframe="1h"))
    for horizon in ("6h", "12h", "24h", "48h"):
        ok = OPS.add_experiment(conn, _short_exp(f"E-{horizon}", factor_id="F-SC",
                                                 timeframe="1h", horizon_duration=horizon,
                                                 target_spec={"timeframe": "1h",
                                                              "horizon": horizon}))
        assert ok["status"] == "inserted"
    with pytest.raises(ValueError, match="horizon_duration must be one of"):
        OPS.add_experiment(conn, _short_exp("E-3h", factor_id="F-SC", timeframe="1h",
                                            horizon_duration="3h",
                                            target_spec={"timeframe": "1h", "horizon": "3h"}))


def test_short_cycle_experiment_does_not_hit_v1_cache_key(conn):
    """相同 (factor,dataset,config,code) 但 target_spec 含 timeframe/horizon 时不算重复。"""
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp("H-SC"))
    OPS.add_factor(conn, _factor("F-SC", "H-SC", timeframe="15m"))
    v1 = OPS.add_experiment(conn, dict(id="E-V1", factor_id="F-SC", dataset_id="DS-EX",
                                       split_role="EXPLORATORY", target_spec={"h": 24},
                                       test_family_id="TF-SC"))
    sc = OPS.add_experiment(conn, _short_exp("E-SC2"))
    assert v1["status"] == "inserted"
    assert sc["status"] == "inserted"
    assert conn.execute("SELECT count(*) FROM experiments").fetchone()[0] == 2


def test_record_tradability_is_append_only_and_validates(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp("H-SC"))
    OPS.add_factor(conn, _factor("F-SC", "H-SC", timeframe="15m"))
    OPS.add_experiment(conn, _short_exp())
    base = dict(experiment_id="E-SC", timeframe="15m", horizon_duration="1h",
                capital_usdt=300, verdict="PASS",
                checks={"min_notional_ok": True, "stop_distance_ok": True})
    r1 = OPS.record_tradability(conn, base)
    r2 = OPS.record_tradability(conn, base)  # 可交易性快照可重复登记（只追加）
    assert r1["status"] == "inserted" and r2["status"] == "inserted"
    assert r1["id"] != r2["id"]
    assert conn.execute("SELECT count(*) FROM tradability").fetchone()[0] == 2
    with pytest.raises(ValueError, match="verdict must be one of"):
        OPS.record_tradability(conn, dict(base, verdict="MAYBE"))
    with pytest.raises(ValueError, match="unknown experiment_id"):
        OPS.record_tradability(conn, dict(base, experiment_id="E-MISSING"))


def test_registry_show_filters_by_timeframe(conn):
    import registry_ops as OPS

    OPS.register_dataset(conn, dataset_id="DS-EX", access_class="EXPLORATORY", path="x",
                         source_version="v1", schema={})
    OPS.add_hypothesis(conn, _hyp("H-SC"))
    OPS.add_factor(conn, _factor("F-15", "H-SC", timeframe="15m"))
    OPS.add_factor(conn, _factor("F-1H", "H-SC", timeframe="1h",
                                 formula_ast={"op": "returns", "n": 1}))
    factors = OPS.registry_show(conn, "factors", timeframe="15m")
    assert [r["id"] for r in factors] == ["F-15"]
    OPS.add_experiment(conn, _short_exp("E-15", "F-15", timeframe="15m", horizon_duration="1h"))
    OPS.add_experiment(conn, _short_exp("E-1H", "F-1H", timeframe="1h", horizon_duration="4h"))
    exps = OPS.registry_show(conn, "experiments", timeframe="1h")
    assert [r["id"] for r in exps] == ["E-1H"]
    with pytest.raises(ValueError, match="timeframe filter not supported"):
        OPS.registry_show(conn, "datasets", timeframe="15m")
    with pytest.raises(ValueError, match="timeframe must be one of"):
        OPS.registry_show(conn, "factors", timeframe="7m")
