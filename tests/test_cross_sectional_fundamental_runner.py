import copy
import hashlib
import json

import pandas as pd
import pytest

from alpha_lab.cross_sectional_runner import run_cross_sectional
from alpha_lab.data.market_panel import MarketPanel
from data_engineering.catalog import publish
from data_engineering.fundamentals import FundamentalDataAdapter


def _panel():
    times = pd.date_range("2024-01-01", periods=32, freq="h", tz="UTC")
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
               "BNB/USDT:USDT", "XRP/USDT:USDT"]
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    price = [100 + i // len(symbols) * (j + 1) + j
             for i, j in enumerate(k % len(symbols) for k in range(len(index)))]
    data = pd.DataFrame({
        "open": price, "high": price, "low": price, "close": price,
        "volume": 100.0,
        "close_time": [time + pd.Timedelta(hours=1) for time, _ in index],
        "available_time": [time + pd.Timedelta(hours=1, seconds=1) for time, _ in index],
    }, index=index)
    return MarketPanel(data), times


def _strict_catalog(root, *, attested):
    catalog = root / "catalog"
    source = root / "input.csv"
    source.write_text(
        "asset_id,metric,event_time,value\n"
        "btc,CapMrktEstUSD,2023-12-31T23:00:00Z,123\n",
        encoding="utf-8",
    )
    manifest = publish(catalog, dataset_id="coinmetrics_btc_dev_v1",
                       kind="fundamental", source="coinmetrics-community",
                       input_path=source)
    if attested:
        evidence_record = {
            "kind": "source_release_record", "dataset_id": manifest["dataset_id"],
            "snapshot_sha256": manifest["sha256"], "source_asset_id": "btc",
            "metric": "CapMrktEstUSD", "event_time": "2023-12-31T23:00:00Z",
            "value": 123, "revision_id": "btc-release-v1",
            "available_at": "2023-12-31T23:01:00Z",
        }
        evidence_bytes = json.dumps(evidence_record, sort_keys=True).encode()
        evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
        evidence_dir = catalog / "pit_evidence"
        evidence_dir.mkdir()
        (evidence_dir / evidence_hash).write_bytes(evidence_bytes)
        sidecar = {
            "schema_version": "pit-evidence-v2", "dataset_id": manifest["dataset_id"],
            "snapshot_sha256": manifest["sha256"], "source": manifest["source"],
            "ingested_at": manifest["ingested_at"], "status": "VERIFIED",
            "review": {"reviewer": "maintainer-test", "audit_reference": "fixture-v1",
                       "reviewed_at": "2026-10-19T00:00:00Z"},
            "evidence": [{"sha256": evidence_hash, "source_uri": "https://fixture/release",
                          "media_type": "application/json", "role": "source_record"}],
            "records": [{"source_asset_id": "btc", "metric": "CapMrktEstUSD",
                         "event_time": "2023-12-31T23:00:00Z", "revision_id": "btc-release-v1",
                         "status": "VERIFIED", "availability_basis": "source_release_record",
                         "available_at": "2023-12-31T23:01:00Z",
                         "evidence_sha256s": [evidence_hash]}],
        }
        sidecar_bytes = (json.dumps(sidecar, sort_keys=True) + "\n").encode()
        sidecar_hash = hashlib.sha256(sidecar_bytes).hexdigest()
        sidecars = catalog / "pit_attestations"
        sidecars.mkdir()
        (sidecars / f"{sidecar_hash}.json").write_bytes(sidecar_bytes)
    return catalog, manifest


def _config(manifest, times):
    return {
        "mode": "cross_sectional", "hypothesis": "strict PIT feature wiring fixture",
        "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
                    "BNB/USDT:USDT", "XRP/USDT:USDT"],
        "timeframe": "1h", "horizon": 2,
        "start": times[0].isoformat(), "end": (times[-1] + pd.Timedelta(hours=1, seconds=2)).isoformat(),
        "train_end": times[16].isoformat(), "test_end": times[30].isoformat(),
        "universe": {"kind": "fixed", "id": "five-v1", "symbols": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
            "BNB/USDT:USDT", "XRP/USDT:USDT"]},
        "feature_schema_version": "alpha-cs-feature-schema-v1",
        "features": [{"kind": "fundamental", "metric": "CapMrktEstUSD",
                       "max_age": "240h", "snapshot_pins": [
                           {"dataset_id": manifest["dataset_id"], "sha256": manifest["sha256"]}]}],
        "n_boot": 19, "block_size": 2,
    }


def test_runner_loads_only_pinned_strict_pit_features_and_binds_identity(tmp_path):
    catalog, manifest = _strict_catalog(tmp_path, attested=True)
    panel, times = _panel()
    config = _config(manifest, times)
    adapter = FundamentalDataAdapter(catalog)

    first = run_cross_sectional(config, panel, root=tmp_path / "research",
                                fundamental_adapter=adapter)
    second = run_cross_sectional(config, panel, root=tmp_path / "research",
                                 fundamental_adapter=adapter)

    assert first["run_id"] == second["run_id"]
    assert len(first["feature_implementation_hash"]) == 64
    assert len(first["candidate_source_hash"]) == 64
    assert first["feature_schema_version"] == "alpha-cs-feature-schema-v1"
    assert first["decision_policy_version"] == "cs-verdict-gated-v2"
    assert first["feature_provenance"]["snapshot_pins"] == [
        [manifest["dataset_id"], manifest["sha256"]]
    ]
    assert len(first["feature_provenance"]["pit_attestation_sha256"]) == 1
    assert first["feature_coverage"]["feature_coverage"][
        "fundamental__token__CapMrktEstUSD"]["coverage"] == 1 / 5
    assert first["feature_coverage"]["status"] == "NOT_EVALUABLE"
    assert first["inference_status"] == "BLOCKED"
    assert all(value["status"] == "NOT_EVALUABLE" for value in first["results"].values())

    changed_config = copy.deepcopy(config)
    changed_config["features"][0]["max_age"] = "239h"
    changed = run_cross_sectional(changed_config, panel, root=tmp_path / "research",
                                  fundamental_adapter=adapter)
    assert changed["run_id"] != first["run_id"]


def test_runner_rejects_unversioned_fundamental_feature_contract(tmp_path):
    catalog, manifest = _strict_catalog(tmp_path, attested=True)
    panel, times = _panel()
    config = _config(manifest, times)
    config.pop("feature_schema_version")

    with pytest.raises(ValueError, match="feature_schema_version"):
        run_cross_sectional(config, panel, root=tmp_path / "research",
                            fundamental_adapter=FundamentalDataAdapter(catalog))

    assert not (tmp_path / "research" / "experiments.sqlite3").exists()


def test_runner_rejects_unverified_fundamentals_before_creating_experiment(tmp_path):
    catalog, manifest = _strict_catalog(tmp_path, attested=False)
    panel, times = _panel()
    config = _config(manifest, times)

    with pytest.raises(ValueError, match="strict_pit rejects"):
        run_cross_sectional(config, panel, root=tmp_path / "research",
                            fundamental_adapter=FundamentalDataAdapter(catalog))

    assert not (tmp_path / "research" / "experiments.sqlite3").exists()
