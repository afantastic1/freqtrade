import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from data_engineering.__main__ import main
from data_engineering.catalog_v3 import register_dataset
from data_engineering.catalog_inventory import audit_targets
from data_engineering.freqtrade_feather import register_feather_file


def _target(**changes):
    value = {
        "exchange": "binance",
        "market_type": "futures",
        "asset": "BTC",
        "exchange_symbol": "BTC/USDT:USDT",
        "data_kind": "ohlcv",
        "timeframe": "5m",
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:10:00Z",
    }
    value.update(changes)
    return value


def _catalog(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "BTC_USDT_USDT-5m-futures.feather"
    pd.DataFrame({
        "date": pd.to_datetime(
            ["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"], utc=True
        ),
        "open": [100.0, 100.0],
        "high": [101.0, 101.0],
        "low": [99.0, 99.0],
        "close": [100.5, 100.5],
        "volume": [2.0, 2.0],
    }).to_feather(source)
    catalog_root = tmp_path / "catalog"
    record = register_feather_file(
        catalog_root,
        source_root_id="binance-futures",
        source_root=source_root,
        relative_path=Path(source.name),
        exchange="binance",
        market_type="futures",
        asset="BTC",
        exchange_symbol="BTC/USDT:USDT",
        data_kind="ohlcv",
        timeframe="5m",
        source="unit-test",
    )
    return catalog_root, {"binance-futures": source_root}, source, record


def test_audit_marks_verified_complete_target_present_and_missing_target_missing(tmp_path):
    catalog_root, roots, _, record = _catalog(tmp_path)

    result = audit_targets(catalog_root, roots, {
        "targets": [_target(), _target(asset="ETH", exchange_symbol="ETH/USDT:USDT")]
    })

    assert result["read_only"] is True
    assert result["summary"] == {
        "INVALID": 0, "MISSING": 1, "PARTIAL": 0, "PRESENT": 1, "target_count": 2
    }
    by_asset = {row["identity"]["asset"]: row for row in result["targets"]}
    assert by_asset["BTC"]["status"] == "PRESENT"
    assert by_asset["BTC"]["dataset_id"] == record["manifest"]["dataset_id"]
    assert by_asset["BTC"]["skip_download"] is True
    assert by_asset["ETH"]["status"] == "MISSING"
    assert by_asset["ETH"]["skip_download"] is False


def test_audit_marks_incomplete_interval_partial(tmp_path):
    catalog_root, roots, _, _ = _catalog(tmp_path)

    result = audit_targets(catalog_root, roots, {
        "targets": [_target(end="2025-01-01T00:15:00Z")]
    })

    row = result["targets"][0]
    assert row["status"] == "PARTIAL"
    assert row["skip_download"] is False
    assert row["missing_interval_count"] == 1
    assert row["missing_intervals"] == [[
        "2025-01-01T00:10:00+00:00", "2025-01-01T00:15:00+00:00"
    ]]


def test_audit_marks_tampered_registered_source_invalid(tmp_path):
    catalog_root, roots, source, _ = _catalog(tmp_path)
    frame = pd.read_feather(source)
    frame.loc[1, "close"] = 999.0
    frame.to_feather(source)

    result = audit_targets(catalog_root, roots, {"targets": [_target()]})

    assert result["targets"][0]["status"] == "INVALID"
    assert "SOURCE_HASH_MISMATCH" in result["targets"][0]["issue_codes"]
    assert result["targets"][0]["skip_download"] is False


def test_audit_rejects_manifest_whose_identity_does_not_match_dataset_id(tmp_path):
    _, roots, _, record = _catalog(tmp_path)
    catalog_root = tmp_path / "mismatched-catalog"
    manifest = deepcopy(record["manifest"])
    manifest["asset"] = "ETH"
    register_dataset(catalog_root, manifest)

    result = audit_targets(catalog_root, roots, {"targets": [_target()]})

    row = result["targets"][0]
    assert row["status"] == "INVALID"
    assert row["issue_codes"] == ["DATASET_IDENTITY_MISMATCH"]
    assert row["skip_download"] is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"targets": [_target(), _target()]}, "duplicate target identity"),
        ({"targets": [{**_target(), "path": "/tmp/data"}]}, "unknown fields"),
        ({"targets": [_target(start="2025-01-01T00:00:00")]}, "timezone-aware UTC"),
        ({"targets": [_target(timeframe="nonsense")]}, "invalid timeframe"),
    ],
)
def test_audit_rejects_ambiguous_or_unsafe_targets(tmp_path, payload, message):
    with pytest.raises(ValueError, match=message):
        audit_targets(tmp_path / "catalog", {}, payload)


def test_catalog_v3_audit_cli_is_read_only_and_emits_deterministic_json(tmp_path, capsys):
    catalog_root, roots, _, _ = _catalog(tmp_path)
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"targets": [_target()]}), encoding="utf-8")
    before = sorted(path.relative_to(catalog_root) for path in catalog_root.rglob("*"))

    assert main([
        "--root", str(catalog_root), "catalog-v3", "audit",
        "--targets", str(targets),
        "--source-root", f"binance-futures={roots['binance-futures']}",
    ]) == 0

    response = json.loads(capsys.readouterr().out)
    after = sorted(path.relative_to(catalog_root) for path in catalog_root.rglob("*"))
    assert response["summary"]["PRESENT"] == 1
    assert response["read_only"] is True
    assert before == after
