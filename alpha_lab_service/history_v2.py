"""Unified, read-only history projection across existing research Stores."""

from __future__ import annotations

from collections.abc import Mapping


_SOURCES = ("legacy", "python-factor-v1", "python-factor-v2")
_STORE_IDS = {
    "legacy": ("legacy-research", "legacy"),
    "python-factor-v1": ("alpha-lab-python-factor-store-v1", "1"),
    "python-factor-v2": ("alpha-lab-python-factor-store-v2", "2"),
}


class UnifiedResearchHistory:
    """Merge sanitized summaries from already configured read-only Store adapters."""

    def __init__(self, stores: Mapping[str, object]):
        if not isinstance(stores, Mapping) or set(stores) - set(_SOURCES):
            raise ValueError("history stores must use recognized source names")
        self._stores = dict(stores)

    def list_history(self, limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history limit outside allowed range")
        statuses = {name: "NOT_CONFIGURED" for name in _SOURCES}
        errors = {}
        entries = []
        for name, store in self._stores.items():
            try:
                if name == "legacy":
                    rows = store.list_runs(limit=limit)
                    rows = [{
                        "kind": "RUN", "identity": row["run_id"],
                        "created_at": row.get("started_at") or "",
                        "payload": {key: row.get(key) for key in (
                            "status", "candidate_name", "classification", "rank_ic",
                            "evaluation_mode", "incremental_status",
                        )},
                    } for row in rows]
                else:
                    rows = store.list_history(limit=limit)
                store_id, protocol = _STORE_IDS[name]
                for row in rows:
                    entries.append({
                        "kind": row["kind"], "identity": row["identity"],
                        "created_at": row.get("created_at") or "",
                        "store_id": store_id, "protocol_version": protocol,
                        "payload": row.get("payload", {}),
                    })
                statuses[name] = "AVAILABLE"
            except Exception as exc:
                statuses[name] = "UNAVAILABLE"
                errors[name] = type(exc).__name__
        entries.sort(key=lambda row: (row["created_at"], row["store_id"], row["identity"]), reverse=True)
        return {
            "entries": [{key: value for key, value in row.items() if key != "created_at"}
                        for row in entries[:limit]],
            "source_status": statuses,
            "source_errors": errors,
        }
