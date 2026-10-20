import json
from importlib.resources import files
from pathlib import Path
import tomllib

from jsonschema import Draft202012Validator


PACKAGE = Path(__file__).parents[1] / "data_engineering"


def test_data_engineering_has_standalone_distribution_metadata():
    metadata = tomllib.loads((PACKAGE / "pyproject.toml").read_text())

    assert metadata["project"]["name"] == "alpha-lab-data-engineering"
    assert metadata["project"]["scripts"]["data-engineering"] == "data_engineering.__main__:main"
    assert "data_engineering.scripts" in metadata["tool"]["setuptools"]["packages"]
    assert "pandas" in " ".join(metadata["project"]["dependencies"])
    assert "freqtrade" not in " ".join(metadata["project"]["dependencies"])


def test_packaged_catalog_schema_accepts_legacy_and_current_manifests():
    schema_dir = files("data_engineering").joinpath("schemas")
    legacy_schema = json.loads(schema_dir.joinpath("catalog-manifest-v1.schema.json").read_text())
    current_schema = json.loads(schema_dir.joinpath("catalog-manifest-v2.schema.json").read_text())
    v3_schema_resource = schema_dir.joinpath("dataset-manifest-v3.schema.json")
    assert v3_schema_resource.is_file()
    v3_schema = json.loads(v3_schema_resource.read_text())
    base = {
        "dataset_id": "unit-test", "kind": "fundamental", "source": "unit-source",
        "source_reference": "", "access_class": "EXPLORATORY_2022_2024",
        "sha256": "a" * 64, "bytes": 50,
        "quality": {"rows": 1, "metrics": ["metric"],
                    "publication_unknown_rows": 1, "point_in_time": "unverified"},
        "research_safe": False, "note": "synthetic fixture",
    }
    legacy = {**base, "schema_version": "data-catalog-v1",
              "published_at": "2026-10-19T00:00:00Z"}
    current = {**base, "schema_version": "data-catalog-v2",
               "ingested_at": "2026-10-19T00:00:00Z"}

    Draft202012Validator(legacy_schema).validate(legacy)
    Draft202012Validator(current_schema).validate(current)
    assert v3_schema["properties"]["schema_version"]["const"] == "data-catalog-v3"


def test_packaged_pit_sidecar_schema_is_available_and_versioned():
    schema = json.loads(
        files("data_engineering").joinpath("schemas", "pit-evidence-v2.schema.json")
        .read_text()
    )
    assert schema["properties"]["schema_version"]["const"] == "pit-evidence-v2"
    assert schema["$defs"]["record"]["properties"]["status"]["enum"] == [
        "VERIFIED", "CONSERVATIVE", "UNVERIFIED", "REJECTED"
    ]
