"""受控、仅追加（append-only）的研究注册接口。

设计原则（对应 docs/freqtrade_alpha_research_skill_design.md §5.2 / §7 / §11）：

- **只做 INSERT**；绝不 UPDATE / DELETE 历史行。纠错只能通过新 revision 或
  `evaluations.supersedes_id` 关联，禁止覆盖导致不可追溯。
- **全部参数化查询**；不执行任意 SQL，不接受外部公式字符串，不做 `eval`。
- 写目标被 `registry.resolve_db_path` 限制在 `user_data/research` 内（路径白名单）。
- 每次写入追加一条 `audit_events`（工具层审计轨迹）。
- **数据集权限门禁（代码强制，不只依赖提示词）**：对 `LOCKED` 数据集登记实验会被拒绝，
  除非该数据集已被人工批准（`data_exposures.approved_by` 非空）。`INDEPENDENT_TEST`
  同理需要批准。这使「2026 锁定区不可用于自主搜索/评价」成为工具层硬约束。
- 幂等：相同主键重复写入返回 `already_exists`（`strict=True` 时改为抛错），不产生重复行。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

ALLOWED_ACCESS = ("EXPLORATORY", "REFERENCE_CONTAMINATED", "LOCKED", "INDEPENDENT_TEST")
#: 需要人工批准才能进入实验的访问级别
GATED_ACCESS = ("LOCKED", "INDEPENDENT_TEST")
ALLOWED_HYP_STATUS = ("PROPOSED", "IMPLEMENTED", "EVALUATED", "REJECTED",
                      "INSUFFICIENT", "PROMISING", "VALIDATED", "ARCHIVED")
ALLOWED_GATE = ("REJECTED", "INSUFFICIENT", "PROMISING", "VALIDATED",
                # P6 三档判定：低功效窗口必须能把「没力量」与「被证伪」分开记录。
                "INCONCLUSIVE_LOW_POWER")
ALLOWED_EVIDENCE = ("EXPLORATORY", "INTERNAL_OOS", "INDEPENDENT_OOS", "DEPLOYMENT_READY")
#: 允许的 K 线周期（与预测窗口解耦；5m/15m 为原生，30m/2h 由聚合派生）
ALLOWED_TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h")
#: 允许的预测窗口（时长口径；来自 schema 002，旧记录可空）。
#: 扩展 "6h/12h/24h/48h"：P5/P6 的正式预测窗口就是这些小时间隔，需要进入**类型化**字段
#: （而非只写在自由文本里），否则「冻结预测窗口」无法被注册表校验。
ALLOWED_HORIZONS = ("15m", "30m", "1h", "2h", "4h", "6h", "12h", "24h", "48h")
ALLOWED_TRADABILITY_VERDICT = ("PASS", "FAIL", "INSUFFICIENT")


def _check_timeframe(value: str | None) -> None:
    if value is not None and value not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {ALLOWED_TIMEFRAMES}")


def _check_horizon(value: str | None) -> None:
    if value is not None and value not in ALLOWED_HORIZONS:
        raise ValueError(f"horizon_duration must be one of {ALLOWED_HORIZONS}")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _require(payload: dict, keys: Iterable[str]) -> None:
    missing = [k for k in keys if payload.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


def _audit(conn: sqlite3.Connection, event_type: str, subject_type: str,
           subject_id: str | None, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_events(event_type, subject_type, subject_id, detail_json) "
        "VALUES (?, ?, ?, ?)",
        (event_type, subject_type, subject_id, canonical(detail)),
    )


def _exists(conn: sqlite3.Connection, table: str, idcol: str, value: str) -> bool:
    if table not in ("datasets", "hypotheses", "factors", "experiments", "research_runs"):
        raise ValueError(f"unknown table {table}")
    if idcol != "id":
        raise ValueError(f"unknown id column {idcol}")
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {idcol} = ?", (value,)).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------
def register_dataset(conn: sqlite3.Connection, *, dataset_id: str, access_class: str,
                     path: str, source_version: str, schema: dict | str,
                     start_ts: str | None = None, end_ts: str | None = None,
                     hash_paths: list[str] | None = None, strict: bool = False) -> dict:
    _require({"dataset_id": dataset_id, "access_class": access_class,
              "path": path, "source_version": source_version}, {})
    if access_class not in ALLOWED_ACCESS:
        raise ValueError(f"access_class must be one of {ALLOWED_ACCESS}")
    schema_json = schema if isinstance(schema, str) else canonical(schema)
    file_hashes = {}
    for raw in (hash_paths or []):
        p = Path(raw)
        if not p.is_file():
            raise FileNotFoundError(f"hash_path not found: {raw}")
        file_hashes[str(p)] = file_sha256(p)
    dataset_hash = stable_hash({
        "path": path, "access_class": access_class, "schema": schema_json,
        "source_version": source_version, "files": file_hashes,
    })
    existing = conn.execute(
        "SELECT id FROM datasets WHERE id = ? OR dataset_hash = ?",
        (dataset_id, dataset_hash)).fetchone()
    if existing is not None:
        if strict:
            raise ValueError(f"dataset exists: {existing['id']}")
        return {"status": "already_exists", "dataset_id": existing["id"],
                "dataset_hash": dataset_hash}
    with conn:
        conn.execute(
            "INSERT INTO datasets(id, access_class, path, dataset_hash, schema_json, "
            "start_ts, end_ts, source_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, access_class, path, dataset_hash, schema_json,
             start_ts, end_ts, source_version))
        _audit(conn, "DATASET_REGISTER", "dataset", dataset_id,
               {"access_class": access_class, "hash": dataset_hash,
                "files": list(file_hashes)})
    return {"status": "inserted", "dataset_id": dataset_id, "dataset_hash": dataset_hash,
            "file_hashes": file_hashes}


def _dataset_access(conn: sqlite3.Connection, dataset_id: str) -> str:
    row = conn.execute("SELECT access_class FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown dataset_id: {dataset_id} (register it first)")
    return row["access_class"]


def _is_approved(conn: sqlite3.Connection, dataset_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM data_exposures WHERE dataset_id = ? AND approved_by IS NOT NULL "
        "AND approved_by != '' LIMIT 1", (dataset_id,)).fetchone()
    return row is not None


def record_exposure(conn: sqlite3.Connection, *, dataset_id: str, purpose: str,
                    approved_by: str, run_id: str | None = None) -> dict:
    """人工批准对某个（通常为 LOCKED / INDEPENDENT_TEST）数据集的一次只读访问。"""
    _dataset_access(conn, dataset_id)
    _require({"approved_by": approved_by, "purpose": purpose}, {})
    with conn:
        conn.execute(
            "INSERT INTO data_exposures(dataset_id, run_id, purpose, approved_by) "
            "VALUES (?, ?, ?, ?)", (dataset_id, run_id, purpose, approved_by))
        _audit(conn, "DATA_EXPOSURE_APPROVED", "dataset", dataset_id,
               {"purpose": purpose, "approved_by": approved_by})
    return {"status": "inserted", "dataset_id": dataset_id, "approved_by": approved_by}


# --------------------------------------------------------------------------
# hypothesis
# --------------------------------------------------------------------------
def add_hypothesis(conn: sqlite3.Connection, payload: dict, strict: bool = False) -> dict:
    _require(payload, ["id", "thesis", "mechanism", "expected_sign", "horizon",
                       "falsification", "semantic_family"])
    if int(payload["expected_sign"]) not in (-1, 0, 1):
        raise ValueError("expected_sign must be -1, 0 or 1")
    status = payload.get("status", "PROPOSED")
    if status not in ALLOWED_HYP_STATUS:
        raise ValueError(f"status must be one of {ALLOWED_HYP_STATUS}")
    data_req = payload.get("data_requirements", [])
    _check_timeframe(payload.get("frequency"))
    if _exists(conn, "hypotheses", "id", payload["id"]):
        if strict:
            raise ValueError(f"hypothesis exists: {payload['id']}")
        return {"status": "already_exists", "id": payload["id"]}
    with conn:
        conn.execute(
            "INSERT INTO hypotheses(id, thesis, mechanism, expected_sign, horizon, "
            "data_requirements_json, falsification, semantic_family, parent_id, status, "
            "frequency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["id"], payload["thesis"], payload["mechanism"],
             int(payload["expected_sign"]), payload["horizon"], canonical(data_req),
             payload["falsification"], payload["semantic_family"],
             payload.get("parent_id"), status, payload.get("frequency")))
        _audit(conn, "HYPOTHESIS_ADD", "hypothesis", payload["id"],
               {"family": payload["semantic_family"], "horizon": payload["horizon"]})
    return {"status": "inserted", "id": payload["id"]}


# --------------------------------------------------------------------------
# factor
# --------------------------------------------------------------------------
def add_factor(conn: sqlite3.Connection, payload: dict, strict: bool = False) -> dict:
    _require(payload, ["id", "hypothesis_id", "name", "formula_ast", "semantic_family",
                       "inputs", "lookback", "frequency", "availability_rule",
                       "direction", "parameters"])
    if int(payload["direction"]) not in (-1, 0, 1):
        raise ValueError("direction must be -1, 0 or 1")
    if int(payload["lookback"]) <= 0:
        raise ValueError("lookback must be > 0")
    if not _exists(conn, "hypotheses", "id", payload["hypothesis_id"]):
        raise ValueError(f"unknown hypothesis_id: {payload['hypothesis_id']}")
    _check_timeframe(payload.get("timeframe"))
    formula = payload["formula_ast"]
    formula_hash = stable_hash(formula)
    instance_hash = stable_hash({"formula": formula, "parameters": payload["parameters"]})
    dupe = conn.execute(
        "SELECT id FROM factors WHERE instance_hash = ?", (instance_hash,)).fetchone()
    if dupe is not None:
        if strict:
            raise ValueError(f"factor instance exists: {dupe['id']}")
        return {"status": "already_exists", "id": dupe["id"], "instance_hash": instance_hash}
    if _exists(conn, "factors", "id", payload["id"]):
        if strict:
            raise ValueError(f"factor id exists: {payload['id']}")
        return {"status": "already_exists", "id": payload["id"]}
    code_hash = payload.get("code_hash") or stable_hash(
        {"implementation": payload.get("implementation", "p5-factor-v1")})
    with conn:
        conn.execute(
            "INSERT INTO factors(id, hypothesis_id, name, formula_ast, formula_hash, "
            "instance_hash, semantic_family, source, license, inputs_json, lookback, "
            "frequency, availability_rule, direction, parameter_json, code_hash, status, "
            "timeframe) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["id"], payload["hypothesis_id"], payload["name"],
             canonical(formula), formula_hash, instance_hash, payload["semantic_family"],
             payload.get("source", "unspecified"), payload.get("license", "unspecified"),
             canonical(payload["inputs"]), int(payload["lookback"]), payload["frequency"],
             payload["availability_rule"], int(payload["direction"]),
             canonical(payload["parameters"]), code_hash, payload.get("status", "IMPLEMENTED"),
             payload.get("timeframe")))
        _audit(conn, "FACTOR_ADD", "factor", payload["id"],
               {"formula_hash": formula_hash, "instance_hash": instance_hash,
                "source": payload.get("source", "unspecified")})
    return {"status": "inserted", "id": payload["id"], "formula_hash": formula_hash,
            "instance_hash": instance_hash}


# --------------------------------------------------------------------------
# experiment（含 LOCKED 门禁）
# --------------------------------------------------------------------------
def add_experiment(conn: sqlite3.Connection, payload: dict, strict: bool = False) -> dict:
    _require(payload, ["id", "factor_id", "dataset_id", "split_role", "target_spec",
                       "test_family_id"])
    if not _exists(conn, "factors", "id", payload["factor_id"]):
        raise ValueError(f"unknown factor_id: {payload['factor_id']}")
    access = _dataset_access(conn, payload["dataset_id"])
    if access in GATED_ACCESS and not _is_approved(conn, payload["dataset_id"]):
        raise PermissionError(
            f"dataset {payload['dataset_id']} access_class={access} requires explicit human "
            f"approval (record_exposure/approved_by) before it can be used in an experiment")
    dataset_hash = conn.execute("SELECT dataset_hash FROM datasets WHERE id = ?",
                                (payload["dataset_id"],)).fetchone()["dataset_hash"]
    config_hash = stable_hash(payload.get("config", {}))
    target_hash = stable_hash(payload["target_spec"])
    code_commit = payload.get("code_commit", "unknown")
    _check_timeframe(payload.get("timeframe"))
    _check_horizon(payload.get("horizon_duration"))
    capital_usdt = payload.get("capital_usdt")
    if capital_usdt is not None and float(capital_usdt) <= 0:
        raise ValueError("capital_usdt must be > 0 when provided")
    dupe = conn.execute(
        "SELECT id FROM experiments WHERE factor_id = ? AND dataset_hash = ? AND "
        "config_hash = ? AND code_commit = ? AND target_spec_hash = ?",
        (payload["factor_id"], dataset_hash, config_hash, code_commit, target_hash)).fetchone()
    if dupe is not None:
        if strict:
            raise ValueError(f"experiment exists: {dupe['id']}")
        return {"status": "already_exists", "id": dupe["id"]}
    if _exists(conn, "experiments", "id", payload["id"]):
        if strict:
            raise ValueError(f"experiment id exists: {payload['id']}")
        return {"status": "already_exists", "id": payload["id"]}
    with conn:
        conn.execute(
            "INSERT INTO experiments(id, factor_id, run_id, dataset_id, dataset_hash, "
            "split_role, config_hash, code_commit, target_spec_json, target_spec_hash, "
            "test_family_id, experiment_type, status, timeframe, horizon_duration, "
            "capital_usdt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["id"], payload["factor_id"], payload.get("run_id"),
             payload["dataset_id"], dataset_hash, payload["split_role"], config_hash,
             code_commit, canonical(payload["target_spec"]), target_hash,
             payload["test_family_id"], payload.get("experiment_type", "factor"),
             payload.get("status", "RUN"), payload.get("timeframe"),
             payload.get("horizon_duration"),
             float(capital_usdt) if capital_usdt is not None else None))
        _audit(conn, "EXPERIMENT_ADD", "experiment", payload["id"],
               {"factor_id": payload["factor_id"], "split_role": payload["split_role"],
                "access_class": access})
    return {"status": "inserted", "id": payload["id"], "dataset_hash": dataset_hash,
            "config_hash": config_hash, "target_spec_hash": target_hash}


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def add_evaluation(conn: sqlite3.Connection, payload: dict, strict: bool = False) -> dict:
    _require(payload, ["experiment_id", "horizon", "metrics", "gate_decision", "evidence_level"])
    if payload["gate_decision"] not in ALLOWED_GATE:
        raise ValueError(f"gate_decision must be one of {ALLOWED_GATE}")
    if payload["evidence_level"] not in ALLOWED_EVIDENCE:
        raise ValueError(f"evidence_level must be one of {ALLOWED_EVIDENCE}")
    if not _exists(conn, "experiments", "id", payload["experiment_id"]):
        raise ValueError(f"unknown experiment_id: {payload['experiment_id']}")
    # 缓存语义：一个 experiment（factor×dataset×config×code×target）只对应一条主评价。
    # 修正不覆盖历史：新建一个 experiment revision，并用 supersedes_id 指向旧的评价 id。
    existing = conn.execute("SELECT id FROM evaluations WHERE experiment_id = ?",
                            (payload["experiment_id"],)).fetchone()
    if existing is not None:
        if strict:
            raise ValueError(f"evaluation exists for experiment {payload['experiment_id']}")
        return {"status": "already_exists", "experiment_id": payload["experiment_id"],
                "evaluation_id": int(existing["id"])}
    if payload.get("supersedes_id") is not None and not conn.execute(
            "SELECT 1 FROM evaluations WHERE id = ?", (int(payload["supersedes_id"]),)).fetchone():
        raise ValueError(f"unknown supersedes_id: {payload['supersedes_id']}")
    with conn:
        cur = conn.execute(
            "INSERT INTO evaluations(experiment_id, horizon, metric_schema_version, "
            "metrics_json, intervals_json, q_value, gate_decision, failure_reason, "
            "evidence_level, supersedes_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["experiment_id"], payload["horizon"],
             int(payload.get("metric_schema_version", 1)), canonical(payload["metrics"]),
             canonical(payload.get("intervals", {})), payload.get("q_value"),
             payload["gate_decision"], payload.get("failure_reason"),
             payload["evidence_level"], payload.get("supersedes_id")))
        eid = int(cur.lastrowid)
        _audit(conn, "EVALUATION_ADD", "evaluation", str(eid),
               {"experiment_id": payload["experiment_id"],
                "gate_decision": payload["gate_decision"]})
    return {"status": "inserted", "id": eid, "experiment_id": payload["experiment_id"]}


# --------------------------------------------------------------------------
# tradability（300U 级可交易性评价，append-only）
# --------------------------------------------------------------------------
def record_tradability(conn: sqlite3.Connection, payload: dict) -> dict:
    """登记一次针对实验的 300U 级可交易性评价。

    只 INSERT：可交易性是执行约束在某一时点的快照，修正必须新增一条记录，
    不得覆盖历史（历史记忆与去重依赖其不可变性）。
    """
    _require(payload, ["experiment_id", "timeframe", "capital_usdt", "verdict", "checks"])
    _check_timeframe(payload["timeframe"])
    _check_horizon(payload.get("horizon_duration"))
    if payload["verdict"] not in ALLOWED_TRADABILITY_VERDICT:
        raise ValueError(f"verdict must be one of {ALLOWED_TRADABILITY_VERDICT}")
    if float(payload["capital_usdt"]) <= 0:
        raise ValueError("capital_usdt must be > 0")
    if not _exists(conn, "experiments", "id", payload["experiment_id"]):
        raise ValueError(f"unknown experiment_id: {payload['experiment_id']}")
    with conn:
        cur = conn.execute(
            "INSERT INTO tradability(experiment_id, timeframe, horizon_duration, "
            "capital_usdt, verdict, checks_json) VALUES (?, ?, ?, ?, ?, ?)",
            (payload["experiment_id"], payload["timeframe"],
             payload.get("horizon_duration"), float(payload["capital_usdt"]),
             payload["verdict"], canonical(payload["checks"])))
        tid = int(cur.lastrowid)
        _audit(conn, "TRADABILITY_RECORD", "tradability", str(tid),
               {"experiment_id": payload["experiment_id"], "verdict": payload["verdict"],
                "capital_usdt": float(payload["capital_usdt"])})
    return {"status": "inserted", "id": tid, "experiment_id": payload["experiment_id"],
            "verdict": payload["verdict"]}


# --------------------------------------------------------------------------
# research run / selection history
# --------------------------------------------------------------------------
def start_run(conn: sqlite3.Connection, payload: dict) -> dict:
    _require(payload, ["id", "purpose"])
    if _exists(conn, "research_runs", "id", payload["id"]):
        return {"status": "already_exists", "id": payload["id"]}
    with conn:
        conn.execute(
            "INSERT INTO research_runs(id, previous_run_id, purpose, proposal_budget, "
            "eval_budget, decision_json, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload["id"], payload.get("previous_run_id"), payload["purpose"],
             int(payload.get("proposal_budget", 0)), int(payload.get("eval_budget", 0)),
             canonical(payload.get("decision", {})), "RUNNING"))
        _audit(conn, "RUN_START", "research_run", payload["id"], {"purpose": payload["purpose"]})
    return {"status": "inserted", "id": payload["id"]}


def finish_run(conn: sqlite3.Connection, *, run_id: str, status: str,
               decision: dict, used_budget: int | None = None) -> dict:
    row = conn.execute("SELECT status FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown run_id: {run_id}")
    if row["status"] != "RUNNING":
        return {"status": "already_finished", "id": run_id, "previous": row["status"]}
    with conn:
        conn.execute(
            "UPDATE research_runs SET status = ?, decision_json = ?, finished_at = "
            "CURRENT_TIMESTAMP, used_budget = COALESCE(?, used_budget) WHERE id = ?",
            (status, canonical(decision), used_budget, run_id))
        _audit(conn, "RUN_FINISH", "research_run", run_id,
               {"status": status, "used_budget": used_budget})
    return {"status": "finished", "id": run_id}


def record_selection(conn: sqlite3.Connection, payload: dict) -> dict:
    _require(payload, ["run_id", "decision", "rationale"])
    if not _exists(conn, "research_runs", "id", payload["run_id"]):
        raise ValueError(f"unknown run_id: {payload['run_id']}")
    with conn:
        cur = conn.execute(
            "INSERT INTO selection_history(run_id, hypothesis_id, factor_id, decision, rationale) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload["run_id"], payload.get("hypothesis_id"), payload.get("factor_id"),
             payload["decision"], payload["rationale"]))
        sid = int(cur.lastrowid)
        _audit(conn, "SELECTION_RECORD", "selection_history", str(sid),
               {"decision": payload["decision"], "run_id": payload["run_id"]})
    return {"status": "inserted", "id": sid}


# --------------------------------------------------------------------------
# legacy registry 归档（append-only 原始保留）
# --------------------------------------------------------------------------
def record_legacy(conn: sqlite3.Connection, payload: dict) -> dict:
    """把旧 registry.jsonl 的一条记录逐字归档（按旧 id 幂等，绝不改写）。

    `raw` 是原始记录字典；`shape` 为形态标签；`mapped_*` 是可选的结构化指针。
    """
    _require(payload, ["id", "raw"])
    raw = payload["raw"]
    if not isinstance(raw, dict):
        raise ValueError("raw must be a JSON object")
    rid = str(payload["id"])
    if conn.execute("SELECT 1 FROM legacy_registry WHERE id = ?", (rid,)).fetchone():
        return {"status": "already_exists", "id": rid}
    with conn:
        conn.execute(
            "INSERT INTO legacy_registry(id, source, shape, alpha_type, legacy_ts, raw_json, "
            "mapped_hypothesis_id, mapped_factor_id, mapped_experiment_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, payload.get("source", "registry.jsonl"),
             str(payload.get("shape", "unknown")), payload.get("alpha_type"),
             payload.get("legacy_ts"), canonical(raw),
             payload.get("mapped_hypothesis_id"), payload.get("mapped_factor_id"),
             payload.get("mapped_experiment_id")))
        _audit(conn, "LEGACY_REGISTRY_IMPORT", "legacy_registry", rid,
               {"source": payload.get("source", "registry.jsonl"),
                "alpha_type": payload.get("alpha_type")})
    return {"status": "inserted", "id": rid}


# --------------------------------------------------------------------------
# 只读查询
# --------------------------------------------------------------------------
_TABLE_COLUMNS = {
    "hypotheses": ("id", "semantic_family", "horizon", "frequency", "status", "created_at"),
    "factors": ("id", "hypothesis_id", "name", "semantic_family", "timeframe", "source",
                "status", "created_at"),
    "experiments": ("id", "factor_id", "dataset_id", "split_role", "timeframe",
                    "horizon_duration", "capital_usdt", "test_family_id", "status"),
    "evaluations": ("id", "experiment_id", "horizon", "gate_decision", "evidence_level", "q_value"),
    "datasets": ("id", "access_class", "path", "dataset_hash"),
    "research_runs": ("id", "purpose", "status", "created_at", "finished_at"),
    "tradability": ("id", "experiment_id", "timeframe", "horizon_duration", "capital_usdt",
                    "verdict", "created_at"),
    "legacy_registry": ("id", "source", "shape", "alpha_type", "mapped_factor_id",
                        "created_at"),
}

#: 支持按 K 线周期过滤的表
_TIMEFRAME_FILTERABLE = ("factors", "experiments", "tradability")


def registry_counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for table in ("hypotheses", "factors", "experiments", "evaluations",
                  "research_runs", "datasets", "data_exposures", "selection_history",
                  "tradability", "legacy_registry"):
        out[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return out


def registry_show(conn: sqlite3.Connection, table: str, *, id: str | None = None,
                  limit: int = 50, timeframe: str | None = None) -> list[dict]:
    if table not in _TABLE_COLUMNS:
        raise ValueError(f"unknown table {table}; choose from {tuple(_TABLE_COLUMNS)}")
    cols = ", ".join(_TABLE_COLUMNS[table])
    clauses, params = [], []
    if id is not None:
        clauses.append("id = ?")
        params.append(id)
    if timeframe is not None:
        if table not in _TIMEFRAME_FILTERABLE:
            raise ValueError(f"timeframe filter not supported for {table}")
        _check_timeframe(timeframe)
        clauses.append("timeframe = ?")
        params.append(timeframe)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT {cols} FROM {table}{where} ORDER BY 1 LIMIT ?", tuple(params)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 批量导入（单个事务、按外键依赖顺序、仅追加）
# --------------------------------------------------------------------------
def import_bundle(conn: sqlite3.Connection, bundle: dict, strict: bool = False) -> dict:
    """按 datasets → hypotheses → factors → experiments → evaluations →
    tradability → selections 的依赖顺序批量写入；run start/finish 可选。
    任何一步失败都不回滚已完成项
    （append-only 语义下部分成功是可见且可审计的，函数返回逐步计数）。
    """
    summary: dict[str, list] = {
        "runs": [], "datasets": [], "hypotheses": [], "factors": [],
        "experiments": [], "evaluations": [], "tradability": [], "legacy": [],
        "selections": [],
    }
    for payload in bundle.get("runs", []):
        summary["runs"].append(start_run(conn, payload))
    for payload in bundle.get("datasets", []):
        summary["datasets"].append(register_dataset(conn, strict=strict, **payload))
    for payload in bundle.get("hypotheses", []):
        summary["hypotheses"].append(add_hypothesis(conn, payload, strict))
    for payload in bundle.get("factors", []):
        summary["factors"].append(add_factor(conn, payload, strict))
    for payload in bundle.get("experiments", []):
        summary["experiments"].append(add_experiment(conn, payload, strict))
    for payload in bundle.get("evaluations", []):
        summary["evaluations"].append(add_evaluation(conn, payload, strict))
    for payload in bundle.get("tradability", []):
        summary["tradability"].append(record_tradability(conn, payload))
    for payload in bundle.get("legacy", []):
        summary["legacy"].append(record_legacy(conn, payload))
    for payload in bundle.get("selections", []):
        summary["selections"].append(record_selection(conn, payload))
    counts = {k: {"inserted": sum(1 for r in v if r.get("status") == "inserted"),
                  "already_exists": sum(1 for r in v if r.get("status") == "already_exists"),
                  "total": len(v)} for k, v in summary.items()}
    return {"counts": counts, "detail": summary}
