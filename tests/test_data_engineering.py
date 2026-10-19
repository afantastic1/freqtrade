import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from data_engineering.catalog import export_market, publish, validate_market, verify
from data_engineering.defillama import normalize_chain_tvl


def _csv(tmp_path, name, rows, fields):
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _market_rows():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        for offset in (0, 1, 3):
            opened = start + timedelta(hours=offset)
            rows.append({"open_time": opened.isoformat(), "symbol": symbol,
                         "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1,
                         "close_time": (opened + timedelta(hours=1)).isoformat(),
                         "available_time": (opened + timedelta(hours=1, seconds=1)).isoformat()})
    return rows


def test_market_publish_verify_export_and_immutable(tmp_path):
    rows = _market_rows()
    input_csv = _csv(tmp_path, "market.csv", rows, list(rows[0]))
    root = tmp_path / "data_store"
    m1 = publish(root, dataset_id="usdm-1h", kind="market", source="trusted-bounded-export",
                 input_path=input_csv, timeframe="1h")
    assert m1["schema_version"] == "data-catalog-v2"
    assert "ingested_at" in m1 and "published_at" not in m1
    m2 = publish(root, dataset_id="usdm-1h", kind="market", source="trusted-bounded-export",
                 input_path=input_csv, timeframe="1h")
    assert m1 == m2
    with pytest.raises(ValueError, match="conflicting provenance"):
        publish(root, dataset_id="usdm-1h", kind="market", source="different-vendor",
                input_path=input_csv, timeframe="1h")
    assert m1["quality"]["symbols"]["BTC/USDT:USDT"]["internal_missing_bars"] == 1
    assert verify(root, dataset_id="usdm-1h", digest=m1["sha256"]) == m1
    exported = export_market(root, dataset_id="usdm-1h", digest=m1["sha256"], output=tmp_path / "panel.csv")
    assert hashlib.sha256(exported.read_bytes()).hexdigest() == m1["sha256"]
    with pytest.raises(FileExistsError):
        export_market(root, dataset_id="usdm-1h", digest=m1["sha256"], output=exported)
    stored = root / "snapshots" / "usdm-1h" / f"{m1['sha256']}.csv"
    stored.write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        verify(root, dataset_id="usdm-1h", digest=m1["sha256"])


def test_catalog_verify_keeps_legacy_v1_manifest_compatible(tmp_path):
    rows = _market_rows()
    input_csv = _csv(tmp_path, "legacy-market.csv", rows, list(rows[0]))
    root = tmp_path / "legacy-store"
    manifest = publish(root, dataset_id="legacy-1h", kind="market",
                       source="trusted-bounded-export", input_path=input_csv,
                       timeframe="1h")
    path = root / "snapshots" / "legacy-1h" / f"{manifest['sha256']}.manifest.json"
    legacy = json.loads(path.read_text())
    legacy["schema_version"] = "data-catalog-v1"
    legacy["published_at"] = legacy.pop("ingested_at")
    path.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")

    assert verify(root, dataset_id="legacy-1h", digest=manifest["sha256"]) == legacy


def test_market_rejects_future_and_unavailable_candles(tmp_path):
    rows = _market_rows()
    rows[0]["open_time"] = "2026-01-01T00:00:00+00:00"
    source = _csv(tmp_path, "future.csv", rows, list(rows[0]))
    with pytest.raises(ValueError, match="development"):
        publish(tmp_path / "store", dataset_id="bad", kind="market", source="test",
                input_path=source, timeframe="1h")
    rows = _market_rows()
    rows[0]["available_time"] = rows[0]["open_time"]
    source = _csv(tmp_path, "early.csv", rows, list(rows[0]))
    with pytest.raises(ValueError, match="before close"):
        validate_market(source.read_bytes(), "1h")


def test_defillama_historical_data_stays_unverified(tmp_path):
    source = tmp_path / "chain.json"
    source.write_text(json.dumps([{"date": 1704067200, "tvl": 100.0},
                                  {"date": 1735689600, "tvl": 150.0}]))
    out = tmp_path / "tvl.csv"
    normalized = normalize_chain_tvl(source, out, chain="Ethereum")
    assert normalized["rows"] == 1
    manifest = publish(tmp_path / "store", dataset_id="eth-chain-tvl", kind="fundamental",
                       source="defillama-offline", input_path=out)
    assert manifest["quality"]["point_in_time"] == "unverified"
    assert not manifest["research_safe"]
    with pytest.raises(ValueError, match="cannot be exported"):
        export_market(tmp_path / "store", dataset_id="eth-chain-tvl", digest=manifest["sha256"], output=tmp_path / "no.csv")


def test_fundamental_declared_time_not_certified(tmp_path):
    source = _csv(tmp_path, "f.csv", [{"asset_id": "chain:Ethereum", "metric": "tvl", "event_time": "2024-01-01T00:00:00Z",
                                       "value": "1", "published_at": "2024-01-02T00:00:00Z"}],
                  ["asset_id", "metric", "event_time", "value", "published_at"])
    meta = publish(tmp_path / "store", dataset_id="funda", kind="fundamental", source="test", input_path=source)
    assert meta["quality"]["point_in_time"] == "declared_not_audited"
    assert not meta["research_safe"]


def test_fetch_is_explicit_fixed_endpoint_and_raw_is_hashed(tmp_path, monkeypatch):
    from data_engineering import defillama
    from urllib import request
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, n): return b'[{"date":1704067200,"tvl":123}]'
    class FakeOpener:
        def open(self, req, timeout):
            assert req.full_url == "https://api.llama.fi/v2/historicalChainTvl/Ethereum"
            assert timeout == 20
            return FakeResponse()
    monkeypatch.setattr(request, "build_opener", lambda *args: FakeOpener())
    record = defillama.fetch_chain_tvl("Ethereum", tmp_path / "raw")
    assert Path(record["raw"]).exists()
    assert "LOCKED" in json.loads(Path(record["receipt"]).read_text())["access"]
    with pytest.raises(ValueError, match="invalid chain"):
        defillama.fetch_chain_tvl("../127.0.0.1", tmp_path / "raw")
