from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def sample_catalog() -> tuple[dict[str, Any], ...]:
    samples = (
        {
            "factor_id": "F-SAMPLE-MOM-024",
            "hypothesis_id": "H-SAMPLE-MOM-024",
            "name": "close_return_momentum_24",
            "thesis": "Recent directional persistence may continue over the next six hours.",
            "mechanism": "Persistent order flow can create short-horizon price momentum.",
            "expected_sign": 1,
            "horizon": "6h",
            "falsification": "Preregistered exploratory IC and conditional value are non-positive.",
            "semantic_family": "price-momentum",
            "formula_ast": {"op": "returns", "field": "close", "periods": 24},
            "inputs": ["close"],
            "lookback": 24,
            "frequency": "1h",
            "availability_rule": "bar_close_ts <= decision_ts",
            "direction": 1,
            "parameters": {"periods": 24},
            "source": "Qlib Alpha158 ROC family; independently expressed for time series",
            "license": "Apache-2.0 source concept; local metadata implementation",
        },
        {
            "factor_id": "F-SAMPLE-VOL-024",
            "hypothesis_id": "H-SAMPLE-VOL-024",
            "name": "realized_volatility_24",
            "thesis": "Elevated realized volatility may predict weaker risk-adjusted forward returns.",
            "mechanism": "Volatility clustering raises near-term execution and downside risk.",
            "expected_sign": -1,
            "horizon": "6h",
            "falsification": "The preregistered negative association is absent after uncertainty adjustment.",
            "semantic_family": "realized-volatility",
            "formula_ast": {
                "op": "rolling_std",
                "input": {"op": "returns", "field": "close", "periods": 1},
                "window": 24,
            },
            "inputs": ["close"],
            "lookback": 25,
            "frequency": "1h",
            "availability_rule": "bar_close_ts <= decision_ts",
            "direction": -1,
            "parameters": {"return_periods": 1, "window": 24},
            "source": "Qlib Alpha158 STD family; independently expressed for time series",
            "license": "Apache-2.0 source concept; local metadata implementation",
        },
    )
    return samples


def import_samples(connection: sqlite3.Connection, dry_run: bool) -> dict[str, Any]:
    samples = sample_catalog()
    existing_ids = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM factors WHERE id IN (?, ?)",
            tuple(sample["factor_id"] for sample in samples),
        )
    }
    if dry_run:
        return {
            "dry_run": True,
            "would_insert": len(samples) - len(existing_ids),
            "existing": len(existing_ids),
            "factors": [sample["factor_id"] for sample in samples],
        }

    inserted = 0
    with connection:
        for sample in samples:
            if sample["factor_id"] in existing_ids:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO hypotheses "
                "(id, thesis, mechanism, expected_sign, horizon, data_requirements_json, "
                "falsification, semantic_family, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sample["hypothesis_id"], sample["thesis"], sample["mechanism"],
                    sample["expected_sign"], sample["horizon"], _canonical(sample["inputs"]),
                    sample["falsification"], sample["semantic_family"], "IMPLEMENTED",
                ),
            )
            formula_hash = _hash(sample["formula_ast"])
            instance_hash = _hash(
                {"formula": sample["formula_ast"], "parameters": sample["parameters"]}
            )
            connection.execute(
                "INSERT INTO factors "
                "(id, hypothesis_id, name, formula_ast, formula_hash, instance_hash, "
                "semantic_family, source, license, inputs_json, lookback, frequency, "
                "availability_rule, direction, parameter_json, code_hash, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sample["factor_id"], sample["hypothesis_id"], sample["name"],
                    _canonical(sample["formula_ast"]), formula_hash, instance_hash,
                    sample["semantic_family"], sample["source"], sample["license"],
                    _canonical(sample["inputs"]), sample["lookback"], sample["frequency"],
                    sample["availability_rule"], sample["direction"],
                    _canonical(sample["parameters"]), _hash({"implementation": "metadata-v1"}),
                    "IMPLEMENTED",
                ),
            )
            inserted += 1
        connection.execute(
            "INSERT INTO audit_events(event_type, subject_type, detail_json) VALUES (?, ?, ?)",
            ("CATALOG_IMPORT", "factor", _canonical({"inserted": inserted, "source": "samples"})),
        )
    return {
        "dry_run": False,
        "inserted": inserted,
        "existing": len(samples) - inserted,
        "factors": [sample["factor_id"] for sample in samples],
    }


def search_factors(connection: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    rows = connection.execute(
        "SELECT id AS factor_id, name, semantic_family, source, status "
        "FROM factors WHERE name LIKE ? OR semantic_family LIKE ? OR source LIKE ? "
        "ORDER BY id LIMIT ?",
        (pattern, pattern, pattern, limit),
    ).fetchall()
    return [dict(row) for row in rows]
