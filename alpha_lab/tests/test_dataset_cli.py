import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import alpha_lab.dataset_cli as cli


def request_file(tmp_path, start, end, split="holdout", symbols=None):
    campaign = json.loads((Path(__file__).parents[2] / "alpha_lab_contracts/campaigns/dataset_builder_acceptance-v1.json").read_text())
    raw = {"protocol_version": "2.0-research-request", "request_id": "cli-test", "campaign_id": campaign["campaign_id"], "policy_version": "1", "campaign_policy_sha256": campaign["policy_sha256"], "start": start, "end": end, "symbols": symbols or ["BTCUSDT"], "timeframe": "5m", "mode": "time_series", "data_types": ["futures_ohlcv"], "split": split, "lookback_bars": 2, "label_horizon_bars": 2, "embargo_bars": 0}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(raw))
    return path


def fake_builder(monkeypatch):
    captured = {}
    class Builder:
        def __init__(self, *args):
            pass
        def build(self, request, *, options):
            captured["request"] = request
            artifact = SimpleNamespace(worker_path="worker.parquet", labels_path="labels.parquet", report_path="report.json", manifest_path="manifest.json", fingerprints={"worker": "a" * 64})
            return SimpleNamespace(manifest={"request_sha256": request.sha256()}, artifact=artifact)
    monkeypatch.setattr(cli, "DatasetBuilderV2", Builder)
    return captured


def argv(tmp_path, request, with_policy=True):
    args = ["dataset", "build", "--request", str(request), "--catalog-root", str(tmp_path / "catalog"), "--source-root", f"binance-data={tmp_path}", "--output-root", str(tmp_path / "out")]
    if with_policy:
        args += ["--missing-policy", "preserve"]
    return args


def test_dataset_cli_builds_dynamic_2026_request(tmp_path, monkeypatch):
    captured = fake_builder(monkeypatch)
    request = request_file(tmp_path, "2026-02-01T00:00:00Z", "2026-02-01T01:00:00Z")
    assert cli.main(argv(tmp_path, request)) == 0
    assert captured["request"].start.year == 2026


def test_dataset_cli_accepts_2025_request(tmp_path, monkeypatch):
    captured = fake_builder(monkeypatch)
    request = request_file(tmp_path, "2025-02-01T00:00:00Z", "2025-02-01T01:00:00Z", split="validation")
    assert cli.main(argv(tmp_path, request)) == 0
    assert captured["request"].start.year == 2025


def test_dataset_cli_rejects_unauthorized_request(tmp_path, monkeypatch, capsys):
    fake_builder(monkeypatch)
    request = request_file(tmp_path, "2026-02-01T00:00:00Z", "2026-02-01T01:00:00Z", symbols=["DOGEUSDT"])
    assert cli.main(argv(tmp_path, request)) == 2
    assert "not authorized" in capsys.readouterr().err


def test_dataset_cli_requires_explicit_missing_policy(tmp_path):
    request = request_file(tmp_path, "2026-02-01T00:00:00Z", "2026-02-01T01:00:00Z")
    with pytest.raises(SystemExit):
        cli.main(argv(tmp_path, request, with_policy=False))


def test_dataset_cli_does_not_mutate_panel_cli_compatibility(tmp_path, monkeypatch):
    fake_builder(monkeypatch)
    panel_cli = Path(__file__).parents[1] / "panel_cli.py"
    before = panel_cli.read_bytes()
    request = request_file(tmp_path, "2026-02-01T00:00:00Z", "2026-02-01T01:00:00Z")
    assert cli.main(argv(tmp_path, request)) == 0
    assert panel_cli.read_bytes() == before
