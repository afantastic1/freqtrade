"""Read-only, operator-configured Data Catalog v3 coverage gateway."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from alpha_lab_contracts._timestamps import parse_rfc3339_utc


_PUBLIC_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_ITEMS_PER_SECTION = 100
_SOURCE_UNAVAILABLE_CODES = {"SOURCE_ROOT_MISSING"}


class CatalogCoverageGatewayV2:
    """Expose verified interval coverage through fixed trusted Catalog roots."""

    def __init__(
        self,
        catalog_root: str | Path,
        source_roots: Mapping[str, str | Path],
        dataset_registry: Mapping[str, str],
    ):
        if not isinstance(source_roots, Mapping) or not isinstance(dataset_registry, Mapping):
            raise ValueError("Catalog source roots and dataset registry must be mappings")
        resolved_registry: dict[str, str] = {}
        for key, dataset_id in dataset_registry.items():
            if (not isinstance(key, str) or not _PUBLIC_KEY.fullmatch(key)
                    or not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id)):
                raise ValueError("invalid trusted dataset registry entry")
            resolved_registry[key] = dataset_id
        self.catalog_root = Path(catalog_root)
        self.source_roots = MappingProxyType({key: Path(value) for key, value in source_roots.items()})
        self.dataset_registry = MappingProxyType(resolved_registry)

    def get_data_coverage(self, *, dataset_key: str, start: str, end: str) -> dict[str, Any]:
        """Return bounded coverage metadata without allowing caller-selected paths."""
        if not isinstance(dataset_key, str) or not _PUBLIC_KEY.fullmatch(dataset_key):
            return {"status": "INVALID_REQUEST", "error_code": "INVALID_DATASET_KEY"}
        dataset_id = self.dataset_registry.get(dataset_key)
        if dataset_id is None:
            return {"status": "NOT_CONFIGURED", "error_code": "DATASET_NOT_CONFIGURED"}
        try:
            start_utc = parse_rfc3339_utc(start, "start")
            end_utc = parse_rfc3339_utc(end, "end")
            if start_utc >= end_utc:
                raise ValueError("start must be before end")
        except (TypeError, ValueError):
            return {"status": "INVALID_REQUEST", "error_code": "INVALID_INTERVAL"}

        from data_engineering.catalog_v3 import get_coverage

        try:
            result = get_coverage(
                self.catalog_root,
                dataset_id,
                start_utc,
                end_utc,
                source_roots=self.source_roots,
            )
        except OSError:
            return {"status": "UNAVAILABLE", "error_code": "CATALOG_UNAVAILABLE"}
        except (TypeError, ValueError):
            return {"status": "INVALID", "error_code": "CATALOG_INVALID"}

        issues = result.get("issues", [])
        issue_codes = sorted({item.get("code", "UNKNOWN") for item in issues})
        if issue_codes and set(issue_codes).issubset(_SOURCE_UNAVAILABLE_CODES):
            return {
                "status": "UNAVAILABLE",
                "error_code": issue_codes[0],
                "dataset_id": dataset_id,
                "requested_interval": result["requested_interval"],
            }

        response = {
            key: value for key, value in result.items()
            if key in {
                "dataset_id", "status", "requested_interval", "observed_intervals",
                "missing_intervals", "missing_interval_count", "missing_observations",
                "observed_count", "partition_versions", "observed_cadence", "coverage_basis",
            }
        }
        truncated: list[str] = []
        for section in ("observed_intervals", "missing_intervals", "partition_versions"):
            entries = response.get(section)
            if isinstance(entries, list) and len(entries) > _MAX_ITEMS_PER_SECTION:
                response[section] = entries[:_MAX_ITEMS_PER_SECTION]
                truncated.append(section)
        if issue_codes:
            response["issues"] = issue_codes[:_MAX_ITEMS_PER_SECTION]
            if len(issue_codes) > _MAX_ITEMS_PER_SECTION:
                truncated.append("issues")
        if truncated:
            response["truncated_sections"] = truncated
        return response
