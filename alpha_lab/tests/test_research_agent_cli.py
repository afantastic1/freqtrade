import json
import sqlite3

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


def test_gateway_redacts_persisted_errors_and_paths_from_history_and_show(tmp_path):
    from alpha_lab.experiment_registry import ExperimentRegistry
    from alpha_lab.research_store import ResearchStore

    db = tmp_path / "experiments.sqlite3"
    registry = ExperimentRegistry(db)
    run_id = registry.create_run(config_hash="redaction", config_json="{}")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE experiments SET status='failed', error=?, finished_at='2024-01-01T00:00:00Z' WHERE run_id=?",
            ("secret=/private/user_data/heldout.csv sentinel=918273", run_id),
        )
    store = ResearchStore(db)
    # Ensure schema setup did not replace or clear the historical failure row.
    assert store.show_run(run_id)["error"].startswith("secret=")
    request = tmp_path / "candidates/current/request.json"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text(json.dumps({"action": "history"}))
    history = execute_request(root=tmp_path)
    assert "error" not in history["runs"][0]
    assert "report_path" not in history["runs"][0]
    assert "918273" not in json.dumps(history)
    request.write_text(json.dumps({"action": "show", "run_id": run_id}))
    shown = execute_request(root=tmp_path)
    assert "error" not in shown["run"]
    assert "source_path" not in shown["run"]
    assert "918273" not in json.dumps(shown)


def test_module_entrypoint_propagates_rejection_exit_code():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("alpha_lab/research_agent_cli.py").read_text())
    guard = next(node for node in tree.body if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Compare))
    assert isinstance(guard.body[0], ast.Raise)
    call = guard.body[0].exc
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    assert call.func.id == "SystemExit"


def test_gateway_rejects_symlink_request(tmp_path):
    folder = tmp_path / "candidates/current"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"action":"history"}')
    (folder / "request.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        execute_request(root=tmp_path)


def test_gateway_rejects_symlinked_panel_directory(tmp_path):
    candidate = tmp_path / "candidates/current"
    candidate.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "panel.csv").write_text("not,a,panel\n")
    (tmp_path / "panels").symlink_to(outside, target_is_directory=True)
    (candidate / "request.json").write_text(
        json.dumps({"action": "cross_sectional_run", "panel_id": "panel"})
    )
    (candidate / "config.json").write_text(json.dumps({
        "start": "2024-01-01T00:00:00Z", "train_end": "2024-06-01T00:00:00Z",
        "test_end": "2024-12-01T00:00:00Z", "end": "2025-01-01T00:00:00Z",
    }))

    with pytest.raises(ValueError, match="symlink"):
        execute_request(root=tmp_path)


def test_gateway_rejects_held_out_request_before_opening_panel(tmp_path, monkeypatch):
    import alpha_lab.research_agent_cli as cli

    candidate = tmp_path / "candidates/current"
    candidate.mkdir(parents=True)
    panel_dir = tmp_path / "panels"
    panel_dir.mkdir()
    (panel_dir / "held.csv").write_text("held-out sentinel must not be opened")
    config = {
        "start": "2024-01-01T00:00:00Z", "train_end": "2024-06-01T00:00:00Z",
        "test_end": "2024-12-01T00:00:00Z", "end": "2025-01-02T00:00:00Z",
    }
    (candidate / "config.json").write_text(json.dumps(config))
    (candidate / "request.json").write_text(
        json.dumps({"action": "cross_sectional_run", "panel_id": "held"})
    )
    monkeypatch.setattr(cli, "load_bounded_panel_csv", lambda *_: pytest.fail("panel opened"))
    with pytest.raises(ValueError, match="research dates outside development interval"):
        execute_request(root=tmp_path)


def test_gateway_rejects_held_out_panel_with_sanitized_error(tmp_path):
    candidate = tmp_path / "candidates/current"
    candidate.mkdir(parents=True)
    panel_dir = tmp_path / "panels"
    panel_dir.mkdir()
    future_time = pd.Timestamp("2025-01-02 00:00:00", tz="UTC")
    frame = pd.DataFrame({
        "open_time": [future_time], "symbol": ["BTC/USDT:USDT"],
        "open": [987654.321], "high": [987654.321], "low": [987654.321],
        "close": [987654.321], "volume": [123456.789],
        "close_time": [future_time + pd.Timedelta(hours=1)],
        "available_time": [future_time + pd.Timedelta(hours=1, seconds=1)],
    })
    frame.to_csv(panel_dir / "future.csv", index=False)
    (candidate / "request.json").write_text(
        json.dumps({"action": "cross_sectional_run", "panel_id": "future"})
    )
    (candidate / "config.json").write_text(json.dumps({
        "start": "2024-01-01T00:00:00Z", "train_end": "2024-06-01T00:00:00Z",
        "test_end": "2024-12-01T00:00:00Z", "end": "2025-01-01T00:00:00Z",
    }))

    with pytest.raises(ValueError, match="held-out data is forbidden") as error:
        execute_request(root=tmp_path)
    assert "987654.321" not in str(error.value)
    assert "2025-01-02" not in str(error.value)


def test_gateway_cli_sanitizes_panel_validation_errors(tmp_path, monkeypatch, capsys):
    import alpha_lab.research_agent_cli as cli

    candidate = tmp_path / "candidates/current"
    candidate.mkdir(parents=True)
    panel_dir = tmp_path / "panels"
    panel_dir.mkdir()
    future_time = pd.Timestamp("2025-01-02 00:00:00", tz="UTC")
    frame = pd.DataFrame({
        "open_time": [future_time], "symbol": ["BTC/USDT:USDT"],
        "open": [987654.321], "high": [987654.321], "low": [987654.321],
        "close": [987654.321], "volume": [123456.789],
        "close_time": [future_time + pd.Timedelta(hours=1)],
        "available_time": [future_time + pd.Timedelta(hours=1, seconds=1)],
    })
    frame.to_csv(panel_dir / "future.csv", index=False)
    (candidate / "request.json").write_text(
        json.dumps({"action": "cross_sectional_run", "panel_id": "future"})
    )
    (candidate / "config.json").write_text("{}")
    execute = cli.execute_request
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "execute_request", lambda: execute(root=tmp_path))

    assert cli.main() == 2
    output = capsys.readouterr().out
    assert "request rejected or failed" in output
    assert "987654.321" not in output
    assert "2025-01-02" not in output


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
              "start": times[0].isoformat(), "end": (times[-1] + pd.Timedelta(hours=1, seconds=2)).isoformat(),
              "train_end": times[12].isoformat(), "test_end": times[26].isoformat(),
              "universe": {"kind": "fixed", "id": "all", "symbols": symbols},
              "features": [{"name": "relative_momentum", "periods": 2}],
              "n_boot": 19, "block_size": 2}
    (candidate / "config.json").write_text(json.dumps(config))
    (candidate / "request.json").write_text(json.dumps({"action": "cross_sectional_run", "panel_id": "synthetic"}))
    result = execute_request(root=tmp_path)
    assert result["mode"] == "cross_sectional"
    assert not marker.exists()

    from alpha_lab.cross_sectional_store import CrossSectionalStore
    request = {"action": "show", "run_id": result["run_id"]}
    (candidate / "request.json").write_text(json.dumps(request))
    shown = execute_request(root=tmp_path)
    evidence_id = CrossSectionalStore(tmp_path / "experiments.sqlite3").evidence_id(result["run_id"])
    assert shown["run"]["cross_sectional_evidence_id"] == evidence_id
