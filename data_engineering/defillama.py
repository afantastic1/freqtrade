"""Normalize an offline DefiLlama chain-TVL response without inventing publish times.

The Data Maintainer retrieves this JSON separately. Raw full-history responses must
not be placed in the Research Agent workspace (they may contain locked years).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from .catalog import START, END, _number, _atomic_create, ID


def normalize_chain_tvl(raw_json: Path, output_csv: Path, *, chain: str) -> dict:
    if not ID.fullmatch(chain):
        raise ValueError("invalid chain name")
    if raw_json.is_symlink() or not raw_json.is_file():
        raise ValueError("raw JSON must be a regular non-symlink file")
    if output_csv.exists() or output_csv.is_symlink():
        raise FileExistsError("normalized data cannot overwrite an existing file")
    document = json.loads(raw_json.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("expected a DefiLlama historicalChainTvl array")
    rows = []
    seen = set()
    for item in document:
        if not isinstance(item, dict) or not {"date", "tvl"} <= item.keys():
            raise ValueError("invalid DefiLlama chain TVL row")
        timestamp = datetime.fromtimestamp(int(item["date"]), tz=timezone.utc)
        if START <= timestamp < END:
            value = _number(str(item["tvl"]))
            if value < 0 or timestamp in seen:
                raise ValueError("negative or duplicate chain TVL")
            seen.add(timestamp)
            rows.append((f"chain:{chain}", "chain_tvl_usd", timestamp.isoformat(), format(value, ".17g"), ""))
    if not rows:
        raise ValueError("no development-period chain TVL rows")
    rows.sort(key=lambda row: row[2])
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["asset_id", "metric", "event_time", "value", "published_at"])
    writer.writerows(rows)
    _atomic_create(output_csv, stream.getvalue().encode("utf-8"))
    return {"rows": len(rows), "chain": chain, "point_in_time": "unverified",
            "warning": "API historical TVL has no audited published_at, use as exploratory proxy only"}


def fetch_chain_tvl(chain: str, raw_root: Path, *, timeout: int = 20, max_bytes: int = 30_000_000) -> dict:
    """Explicit maintainer-only network fetch; research never calls this function.

    Only a fixed HTTPS DefiLlama endpoint is reachable. Redirects are denied.
    The full historical raw response can include holdout years: steward-only access.
    """
    from hashlib import sha256
    from urllib.error import HTTPError
    from urllib.parse import quote
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    if not ID.fullmatch(chain):
        raise ValueError("invalid chain name")
    if timeout < 1 or max_bytes < 100:
        raise ValueError("invalid fetch limits")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("redirects are forbidden")

    url = f"https://api.llama.fi/v2/historicalChainTvl/{quote(chain, safe='')}"
    req = Request(url, headers={"User-Agent": "AlphaLab-DataSteward/0.1", "Accept": "application/json"})
    with build_opener(NoRedirect()).open(req, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("unexpected HTTP status")
        content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("raw response exceeds permitted size")
    if not isinstance(json.loads(content), list):
        raise ValueError("invalid chain TVL API response")
    digest = sha256(content).hexdigest()
    directory = Path(raw_root) / chain
    raw = directory / f"{digest}.json"
    receipt = directory / f"{digest}.receipt.json"
    if not raw.exists():
        _atomic_create(raw, content)
    elif sha256(raw.read_bytes()).hexdigest() != digest:
        raise ValueError("existing raw file was modified")
    if not receipt.exists():
        record = {"url": url, "sha256": digest, "bytes": len(content), "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "access": "MAINTAINER_ONLY_RAW; MAY INCLUDE LOCKED YEARS", "pit_certified": False}
        _atomic_create(receipt, (json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
    return {"raw": str(raw), "receipt": str(receipt), "sha256": digest,
            "warning": "Full-history steward-only raw response; do not give Research Agent access"}
