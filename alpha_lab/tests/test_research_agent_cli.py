import json

import pandas as pd
import pytest

from alpha_lab.research_agent_cli import execute_request


def test_gateway_accepts_fixed_history_request_and_rejects_arguments(tmp_path):
    folder = tmp_path / "candidates/current"
    folder.mkdir(parents=True)
    request = folder / "request.json"
    request.write_text(json.dumps({"action": "history"}))
    assert execute_request(root=tmp_path)["runs"] == []
    request.write_text(json.dumps({"action": "history", "command": "cat /private"}))
    with pytest.raises(ValueError, match="unsupported"):
        execute_request(root=tmp_path)
    request.write_text(json.dumps({"action": "cross_sectional_run", "panel_id": "../../private"}))
    with pytest.raises(ValueError, match="panel_id"):
        execute_request(root=tmp_path)


def test_gateway_rejects_symlink_request(tmp_path):
    folder = tmp_path / "candidates/current"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"action":"history"}')
    (folder / "request.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        execute_request(root=tmp_path)


def test_fixed_transform_gateway_never_imports_malicious_factor(tmp_path):
    candidate = tmp_path / "candidates/current"
    candidate.mkdir(parents=True)
    panel_dir = tmp_path / "panels"
    panel_dir.mkdir()
    marker = tmp_path / "pwned"
    (candidate / "factor.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "import socket, subprocess\n"
        "subprocess.run(['sh', '-c', 'true'])\n"
        "socket.create_connection(('127.0.0.1', 1))\n")
    times = pd.date_range("2024-01-01", periods=28, freq="h", tz="UTC")
    symbols = list("ABCDE")
    index = pd.MultiIndex.from_product([times, symbols], names=["open_time", "symbol"])
    frame = pd.DataFrame({"open": [100 + i // 5 + i % 5 for i in range(len(index))],
                          "volume": 100.,
                          "close_time": [t + pd.Timedelta(hours=1) for t, _ in index],
                          "available_time": [t + pd.Timedelta(hours=1, seconds=1) for t, _ in index]}, index=index)
    frame["open"] = frame["open"].astype(float)
    frame["high"] = frame["open"]
    frame["low"] = frame["open"]
    frame["close"] = frame["open"]
    frame.reset_index().to_csv(panel_dir / "synthetic.csv", index=False)
    config = {"mode": "cross_sectional", "hypothesis": "gateway fixed transform test",
              "symbols": symbols, "timeframe": "1h", "horizon": 2,
              "start": times[0].isoformat(), "end": (times[-1] + pd.Timedelta(hours=1)).isoformat(),
              "train_end": times[12].isoformat(), "test_end": times[26].isoformat(),
              "universe": {"kind": "fixed", "id": "all", "symbols": symbols},
              "features": [{"name": "relative_momentum", "periods": 2}],
              "n_boot": 19, "block_size": 2}
    (candidate / "config.json").write_text(json.dumps(config))
    (candidate / "request.json").write_text(json.dumps({"action": "cross_sectional_run", "panel_id": "synthetic"}))
    result = execute_request(root=tmp_path)
    assert result["mode"] == "cross_sectional"
    assert not marker.exists()
