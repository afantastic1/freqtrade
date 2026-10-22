"""Idempotent bulk registration of existing local Freqtrade Feather datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from .catalog_v3 import validate_dataset
from .data_kind_registry import DATA_KIND_CONTRACTS
from .freqtrade_feather import _parse_filename, _parse_timeframe, register_feather_file
from .sources.contracts import DatasetIdentity


_MARKET_TYPES = ("futures", "spot")
_DATA_KINDS = ("funding_rate", "mark_price", "ohlcv", "open_interest")


def _selected(value: str, allowed: set[str] | None) -> bool:
    return allowed is None or value in allowed


def _selection(values: Iterable[str] | None, *, valid: set[str], name: str) -> set[str] | None:
    if values is None:
        return None
    selected = set(values)
    if not selected:
        raise ValueError(f"{name} filter must not be empty")
    unknown = selected - valid
    if unknown:
        raise ValueError(f"unsupported {name}: {sorted(unknown)}")
    return selected


def import_freqtrade_directory(
    catalog_root: Path,
    datadir: Path,
    *,
    source_root_id: str,
    exchange: str = "binance",
    assets: Iterable[str] | None = None,
    market_types: Iterable[str] | None = None,
    timeframes: Iterable[str] | None = None,
    data_kinds: Iterable[str] | None = None,
    existing_source_roots: Mapping[str, Path] | None = None,
) -> dict:
    """Validate and register recognized local files without downloading or rewriting them."""
    root = Path(datadir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Freqtrade datadir must be an existing nonsymlink directory")
    selected_assets = set(assets) if assets is not None else None
    if selected_assets is not None and (not selected_assets or any(not item for item in selected_assets)):
        raise ValueError("asset filter must contain nonempty values")
    selected_markets = _selection(
        market_types, valid=set(_MARKET_TYPES), name="market type"
    )
    selected_kinds = _selection(data_kinds, valid=set(_DATA_KINDS), name="data kind")
    selected_timeframes = set(timeframes) if timeframes is not None else None
    if selected_timeframes is not None and (not selected_timeframes or any(not item for item in selected_timeframes)):
        raise ValueError("timeframe filter must contain nonempty values")

    locations = {
        "spot": (root, f"{source_root_id}-spot"),
        "futures": (root / "futures", f"{source_root_id}-futures"),
    }
    roots = {str(key): Path(value) for key, value in (existing_source_roots or {}).items()}
    for market_type, (source_root, root_id) in locations.items():
        if source_root.is_dir():
            roots[root_id] = source_root

    candidates = []
    for market_type in _MARKET_TYPES:
        if not _selected(market_type, selected_markets):
            continue
        source_root, root_id = locations[market_type]
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.glob("*.feather"), key=lambda item: item.name):
            try:
                identity = _parse_filename(path.name, exchange=exchange, market_type=market_type)
            except ValueError:
                continue
            if not all((
                _selected(identity["asset"], selected_assets),
                _selected(identity["timeframe"], selected_timeframes),
                _selected(identity["data_kind"], selected_kinds),
            )):
                continue
            if DATA_KIND_CONTRACTS[identity["data_kind"]]["cadence_policy"] == "event":
                identity = {
                    **identity,
                    "timeframe": None,
                    "event_cadence": None,
                    "storage_timeframe": identity["timeframe"],
                }
            candidates.append((market_type, source_root, root_id, path, identity))

    # Event files with different storage sampling labels share one logical identity.
    # Prefer the finest local representation deterministically before de-duplication.
    candidates.sort(key=lambda item: (
        _parse_timeframe(item[4]["storage_timeframe"].lower())
        if "storage_timeframe" in item[4] else 0,
        item[3].name,
    ))

    rows = []
    seen = set()
    for market_type, source_root, root_id, path, identity in candidates:
        base = {**identity, "dataset_id": None, "network_access": False}
        try:
            dataset_identity = DatasetIdentity(
                identity["exchange"], identity["market_type"], identity["asset"],
                identity["exchange_symbol"], identity["data_kind"], identity["timeframe"],
            )
            dataset_id = dataset_identity.dataset_id()
            base["dataset_id"] = dataset_id
            if dataset_id in seen:
                rows.append({
                    **base, "status": "SKIPPED_DUPLICATE",
                })
                continue
            existing = validate_dataset(Path(catalog_root), dataset_id, source_roots=roots)
            if existing["status"] in {"AVAILABLE", "PARTIAL"}:
                seen.add(dataset_id)
                rows.append({
                    **base, "status": "SKIPPED_PRESENT",
                    "quality_status": existing["status"],
                })
                continue
            register_feather_file(
                Path(catalog_root), source_root_id=root_id, source_root=source_root,
                relative_path=Path(path.name), exchange=exchange,
                market_type=market_type, asset=identity["asset"],
                exchange_symbol=identity["exchange_symbol"],
                data_kind=identity["data_kind"], timeframe=identity["timeframe"],
                event_cadence=identity.get("event_cadence"),
                source="freqtrade-local", source_reference=str(path),
            )
            verified = validate_dataset(Path(catalog_root), dataset_id, source_roots=roots)
            if verified["status"] not in {"AVAILABLE", "PARTIAL"}:
                raise ValueError("registered dataset did not pass Catalog validation")
            seen.add(dataset_id)
            rows.append({**base, "status": "REGISTERED", "quality_status": verified["status"]})
        except (OSError, ValueError, KeyError):
            rows.append({**base, "status": "FAILED", "error_code": "SOURCE_INVALID"})

    rows.sort(key=lambda item: (
        item["asset"], item["market_type"], item["data_kind"], item["timeframe"]
    ))
    counts = {status: sum(row["status"] == status for row in rows)
              for status in (
                  "FAILED", "REGISTERED", "SKIPPED_DUPLICATE", "SKIPPED_PRESENT",
              )}
    return {
        "read_only_source": True,
        "network_access": False,
        "source_roots": {key: str(value) for key, value in sorted(roots.items())},
        "summary": {**counts, "total": len(rows)},
        "datasets": rows,
    }


__all__ = ["import_freqtrade_directory"]
