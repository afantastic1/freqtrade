from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from alpha_lab.data.availability import AvailabilityRuleRegistry, AvailabilityRuleV1
from alpha_lab.data.source_resolver import DatasetSourceResolver
from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1, TemporalPlanV1
from data_engineering.sources.contracts import DatasetIdentity


IDENTITY = DatasetIdentity("binance", "futures", "BTC", "BTC/USDT:USDT", "ohlcv", "5m")


def resolver(monkeypatch, candidate_identity=IDENTITY):
    import alpha_lab.data.source_resolver as module
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    record = {"manifest": {"dataset_id": "chosen", "partitions": [{"source_root_id": "source", "source_path": "part.feather", "start": "2025-01-01T00:00:00Z", "end": "2025-02-01T00:00:00Z", "source_sha256": "d" * 64, "fingerprint": "e" * 64}], **{
        "exchange": candidate_identity.exchange, "market_type": candidate_identity.market_type,
        "asset": candidate_identity.asset, "exchange_symbol": candidate_identity.exchange_symbol,
        "data_kind": candidate_identity.data_kind, "timeframe": candidate_identity.timeframe,
    }}, "manifest_sha256": "a" * 64}
    monkeypatch.setattr(module, "list_datasets", lambda root: [record])
    monkeypatch.setattr(module, "validate_dataset", lambda *a, **k: {"status": "AVAILABLE", "manifest": record["manifest"]})
    frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-01T00:00Z"], utc=True), "open": [1.], "high": [2.], "low": [.5], "close": [1.5], "volume": [1.]})
    def read_snapshot(root, dataset_id, digest, *args, **kwargs):
        if digest != "a" * 64:
            raise ValueError("pinned manifest is not registered")
        return SimpleNamespace(manifest=record["manifest"], manifest_sha256=digest, observations=frame, quality={"status": "AVAILABLE"})
    monkeypatch.setattr(module, "read_dataset_snapshot", read_snapshot)
    registry = AvailabilityRuleRegistry({"rule": AvailabilityRuleV1("rule", IDENTITY, "fixed_delay", "date", fixed_delay=__import__("datetime").timedelta(seconds=1))})
    return DatasetSourceResolver("catalog", {"source": "root"}, exchange="binance", symbol_identities={"BTCUSDT": {"exchange": "binance", "market_type": "futures", "asset": "BTC", "exchange_symbol": "BTC/USDT:USDT"}}, availability_registry=registry), TemporalPlanV1(start, end, start, end, start, start, start, end, "5m")


def test_resolver_requires_full_dataset_identity(monkeypatch):
    wrong = DatasetIdentity("binance", "futures", "BTC", "BTC/USDC:USDC", "ohlcv", "5m")
    resolver_, plan = resolver(monkeypatch, wrong)
    request = SimpleNamespace(symbols=("BTCUSDT",), data_types=("futures_ohlcv",), timeframe="5m")
    options = DatasetBuildOptionsV1("reject", None, {}, {"futures_ohlcv": "rule"}, {})
    assert resolver_.resolve(request, plan, options) == ()


def test_resolver_maps_spot_futures_funding_and_oi_without_kind_aliasing():
    from alpha_lab.data.source_resolver import _TYPE
    assert _TYPE == {"futures_ohlcv": ("futures", "ohlcv"), "spot_ohlcv": ("spot", "ohlcv"), "funding_rate": ("futures", "funding_rate"), "open_interest": ("futures", "open_interest")}


def test_resolver_revalidates_exact_snapshot_pin(monkeypatch):
    resolver_, plan = resolver(monkeypatch)
    request = SimpleNamespace(symbols=("BTCUSDT",), data_types=("futures_ohlcv",), timeframe="5m")
    options = DatasetBuildOptionsV1("reject", None, {"chosen": "b" * 64}, {"futures_ohlcv": "rule"}, {})
    with pytest.raises(ValueError, match="pinned manifest"):
        resolver_.resolve(request, plan, options)


def test_resolver_rejects_unauthorized_data_type(monkeypatch):
    resolver_, plan = resolver(monkeypatch)
    request = SimpleNamespace(symbols=("BTCUSDT",), data_types=("unknown",), timeframe="5m")
    options = DatasetBuildOptionsV1("reject", None, {}, {}, {})
    with pytest.raises(ValueError, match="identity mapping"):
        resolver_.resolve(request, plan, options)


def test_resolver_preserves_partial_source_gaps_without_fill(monkeypatch):
    resolver_, plan = resolver(monkeypatch)
    request = SimpleNamespace(symbols=("BTCUSDT",), data_types=("futures_ohlcv",), timeframe="5m")
    options = DatasetBuildOptionsV1("preserve", None, {}, {"futures_ohlcv": "rule"}, {})
    found = resolver_.resolve(request, plan, options)[0]
    assert len(found.observations) == 1


def test_resolver_rejects_unverified_availability(monkeypatch):
    resolver_, plan = resolver(monkeypatch)
    resolver_.availability_registry = AvailabilityRuleRegistry({"rule": AvailabilityRuleV1("rule", IDENTITY, "source_evidence", "date")})
    request = SimpleNamespace(symbols=("BTCUSDT",), data_types=("futures_ohlcv",), timeframe="5m")
    options = DatasetBuildOptionsV1("preserve", None, {}, {"futures_ohlcv": "rule"}, {})
    assert resolver_.resolve(request, plan, options)[0].availability.pit_status.iloc[0] == "UNVERIFIED"
