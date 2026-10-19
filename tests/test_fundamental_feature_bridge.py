import csv
import hashlib
import json
import pandas as pd
import pytest

from alpha_lab.data.cross_sectional_dataset import CrossSectionalDataset
from alpha_lab.data.market_panel import MarketPanel
from data_engineering.catalog import publish
from data_engineering.fundamentals import OUTPUT_COLUMNS, FundamentalDataAdapter
from data_engineering.fundamentals import _STRICT_PIT_READ_TOKEN, _frame_fingerprint
from data_engineering.alpha_lab_features import build_fundamental_features


def _panel():
    stamps = [("2024-03-20T00:00:00Z", ticker) for ticker in ("BTC", "ETH", "SOL", "BNB", "XRP")]
    stamps.append(("2024-03-25T00:00:00Z", "XRP"))
    times = pd.to_datetime([item[0] for item in stamps], utc=True)
    symbols = [f"{item[1]}/USDT:USDT" for item in stamps]
    index = pd.MultiIndex.from_arrays([times, symbols], names=["open_time", "symbol"])
    frame = pd.DataFrame({
        "available_time": times + pd.Timedelta(minutes=1),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
    }, index=index)
    return MarketPanel(frame)


def _record(asset_id, tradable, scope, metric, value, available, source):
    return {
        "asset_id": asset_id, "tradable_asset_id": tradable,
        "entity_scope": scope, "metric": metric,
        "event_time": "2024-03-18T00:00:00Z", "available_time": available,
        "value": value, "source": source, "dataset_id": "dataset-v1",
        "snapshot_sha256": "b" * 64, "pit_status": "verified",
    }


def _observations():
    records = []
    for ticker, value in (("BTC", 1), ("ETH", 2), ("XRP", 5)):
        records.append(_record(
            f"token:{ticker}", ticker, "token", "CapMrktEstUSD", value,
            "2024-03-19T00:00:00Z", "coinmetrics-community",
        ))
    ecosystems = (("Bitcoin", "BTC", 10), ("Ethereum", "ETH", 20),
                  ("Solana", "SOL", 30), ("BSC", "BNB", 40))
    for chain, ticker, value in ecosystems:
        records.append(_record(
            f"ecosystem:{chain}", ticker, "ecosystem", "chain_tvl_usd", value,
            "2024-03-19T00:00:00Z", "defillama-chain-tvl",
        ))
    records.append(_record(
        "ecosystem:XRPL", "XRP", "ecosystem", "chain_tvl_usd", 50,
        "2024-03-24T12:00:00Z", "defillama-chain-tvl",
    ))
    frame = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    frame["event_time"] = pd.to_datetime(frame.event_time, utc=True)
    frame["available_time"] = pd.to_datetime(frame.available_time, utc=True)
    frame.attrs.update(access_mode="strict_pit", _strict_pit_read_token=_STRICT_PIT_READ_TOKEN)
    frame.attrs["_strict_pit_read_fingerprint"] = _frame_fingerprint(frame)
    return frame


def test_bridge_keeps_missing_assets_and_prelisting_xrp_tvl_null():
    panel = _panel()
    features = build_fundamental_features(
        panel, _observations(), metrics=["CapMrktEstUSD", "chain_tvl_usd"],
        max_age="72h",
    )

    token = features["fundamental__token__CapMrktEstUSD"]
    ecosystem = features["fundamental__ecosystem__chain_tvl_usd"]
    symbols = panel.frame.index.get_level_values("symbol")
    times = panel.frame.index.get_level_values("open_time")
    early_xrp = (symbols == "XRP/USDT:USDT") & (times == pd.Timestamp("2024-03-20T00:00:00Z"))
    late_xrp = (symbols == "XRP/USDT:USDT") & (times == pd.Timestamp("2024-03-25T00:00:00Z"))

    assert token.index.equals(panel.frame.index)
    assert pd.isna(token.loc[panel.frame.index[symbols == "SOL/USDT:USDT"]]).all()
    assert pd.isna(token.loc[panel.frame.index[symbols == "BNB/USDT:USDT"]]).all()
    assert pd.isna(ecosystem.loc[panel.frame.index[early_xrp]]).all()
    assert ecosystem.loc[panel.frame.index[late_xrp]].iloc[0] == 50


def test_feature_series_are_accepted_by_existing_cross_sectional_dataset():
    panel = _panel()
    features = build_fundamental_features(
        panel, _observations(), metrics=["CapMrktEstUSD"], max_age="72h"
    )
    available = panel.frame.available_time.rename("decision_time")
    labels = pd.DataFrame({
        "raw_return": 0.01,
        "relative_return": 0.0,
        "decision_time": available,
        "label_available_time": available + pd.Timedelta(days=1),
    }, index=panel.frame.index)
    membership = pd.Series(True, index=panel.frame.index)

    dataset = CrossSectionalDataset(panel, membership, features, labels)

    result = dataset.frame(target="raw_return")
    assert "fundamental__token__CapMrktEstUSD" in result
    assert len(result) == 3
    assert features["fundamental__token__CapMrktEstUSD"].attrs["pit_status"] == "verified"


def test_duplicate_or_unknown_feature_metric_fails_closed():
    panel = _panel()
    observations = _observations()

    with pytest.raises(ValueError, match="duplicate metric"):
        build_fundamental_features(
            panel, observations, metrics=["chain_tvl_usd", "chain_tvl_usd"],
            max_age="72h",
        )
    with pytest.raises(ValueError, match="unknown metric"):
        build_fundamental_features(panel, observations, metrics=["invented"], max_age="72h")


def test_registered_but_unavailable_metric_returns_null_with_explicit_status():
    panel = _panel()
    observations = pd.DataFrame(columns=OUTPUT_COLUMNS)
    observations.attrs["snapshot_pins"] = [("searched_dataset", "c" * 64)]
    observations.attrs["pit_attestation_sha256"] = ["d" * 64]

    features = build_fundamental_features(
        panel, observations, metrics=["chain_tvl_usd"], max_age="72h"
    )
    series = features["fundamental__ecosystem__chain_tvl_usd"]

    assert series.isna().all()
    assert series.attrs["pit_status"] == "not_evaluable"
    assert series.attrs["snapshot_pins"] == [("searched_dataset", "c" * 64)]
    assert series.attrs["pit_attestation_sha256"] == ["d" * 64]
    with pytest.raises(ValueError, match="max_age"):
        build_fundamental_features(
            panel, observations, metrics=["chain_tvl_usd"], max_age="not-a-duration"
        )


def test_end_to_end_attested_catalog_read_asof_and_dataset(tmp_path):
    catalog_root = tmp_path / "catalog"
    sidecars = catalog_root / "pit_attestations"
    sidecars.mkdir(parents=True)
    manifests = []
    for ticker, value in (("btc", 123), ("eth", 234), ("sol", 345),
                          ("bnb", 456), ("xrp", 567)):
        dataset_id = f"coinmetrics_{ticker}_dev_v1"
        source = tmp_path / f"{ticker}.csv"
        with source.open("w", newline="", encoding="utf8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["asset_id", "metric", "event_time", "value", "available_time"])
            writer.writerow([ticker, "CapMrktEstUSD", "2024-03-19T00:00:00Z",
                             value, "2024-03-19T02:00:00Z"])
        manifest = publish(catalog_root, dataset_id=dataset_id,
                           kind="fundamental", source="coinmetrics-community",
                           input_path=source)
        evidence = {"dataset_id": manifest["dataset_id"],
                    "snapshot_sha256": manifest["sha256"], "status": "verified",
                    "reviewer": "steward", "audit_reference": f"audit-{ticker}",
                    "availability_column": "available_time"}
        payload = (json.dumps(evidence, sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(payload).hexdigest()
        (sidecars / f"{digest}.json").write_bytes(payload)
        manifests.append(manifest)

    panel = _panel()
    adapter = FundamentalDataAdapter(catalog_root)
    observations = adapter.read_many(
        [(manifest["dataset_id"], manifest["sha256"]) for manifest in manifests],
        access="strict_pit",
    )
    feature = build_fundamental_features(
        panel, observations, metrics=["CapMrktEstUSD"], max_age="240h"
    )
    symbols = panel.frame.index.get_level_values("symbol")
    expected = {"BTC/USDT:USDT": 123.0, "ETH/USDT:USDT": 234.0,
                "SOL/USDT:USDT": 345.0, "BNB/USDT:USDT": 456.0,
                "XRP/USDT:USDT": 567.0}
    for symbol, value in expected.items():
        mask = symbols == symbol
        assert feature["fundamental__token__CapMrktEstUSD"].loc[panel.frame.index[mask]].eq(value).all()
    assert feature["fundamental__token__CapMrktEstUSD"].attrs["pit_status"] == "verified"
    assert len(observations.attrs["snapshot_pins"]) == 5
    assert len(observations.attrs["pit_attestation_sha256"]) == 5

    available = panel.frame.available_time.rename("decision_time")
    labels = pd.DataFrame({
        "raw_return": 0.01, "relative_return": 0.0,
        "decision_time": available,
        "label_available_time": available + pd.Timedelta(days=1),
    }, index=panel.frame.index)
    dataset = CrossSectionalDataset(
        panel, pd.Series(True, index=panel.frame.index), feature, labels
    )
    assert "fundamental__token__CapMrktEstUSD" in dataset.frame(target="raw_return")
