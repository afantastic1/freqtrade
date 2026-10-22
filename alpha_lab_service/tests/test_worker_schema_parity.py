"""Keep the pinned Worker image's field registry in sync with the service schema.

The Worker image cannot import the service package (it is a standalone, network
isolated runtime), so it duplicates the typed field registry. This test fails
when the two copies drift, which otherwise surfaces only as an opaque
EXECUTION_BLOCKED at run time.
"""

from __future__ import annotations

import ast
from pathlib import Path

from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY

WORKER_PATH = Path(__file__).resolve().parents[2] / "containers/alpha-lab-factor-worker/worker.py"


def _worker_set(name: str) -> set[str]:
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"{name} is not defined in the Worker source")


def test_worker_field_registry_matches_service_schema():
    schema = INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1")
    fields = schema.fields
    assert _worker_set("V2_ALLOWED_FIELDS") == set(fields)
    assert _worker_set("V2_NUMERIC_FIELDS") == {
        name for name, field in fields.items() if field.logical_type == "number"
    }
    assert _worker_set("V2_BOOLEAN_FIELDS") == {
        name for name, field in fields.items() if field.logical_type == "boolean"
    }
