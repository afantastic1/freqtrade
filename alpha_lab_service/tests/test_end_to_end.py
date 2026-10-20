"""Synthetic protocol acceptance using actual disposable Worker containers."""

import asyncio
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alpha_lab_service.broker_server import WorkerBroker
from alpha_lab_service.jobs import ResearchService
from alpha_lab_service.store import ResearchStoreV1
from alpha_lab_service.worker_profile import WorkerProfile

IMAGE = os.environ.get("ALPHA_LAB_WORKER_IMAGE", "")
FIXTURES = Path(__file__).parent / "fixtures/factors"
SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
TIMES = pd.date_range("2022-01-01", "2024-12-01", freq="MS", tz="UTC")


def _synthetic_panel():
    rows = []
    labels = {}
    baseline = {}
    for ti, timestamp in enumerate(TIMES):
        for si, symbol in enumerate(SYMBOLS):
            close = 100 + 0.7 * ti * (si + 1) + np.sin((ti + 2 * si) / 2.5) * (si + 1)
            future = 0.01 * np.sin((ti + 1) / 2 + si) + 0.002 * si
            rows.append((timestamp, symbol, timestamp, close, close * 1.002, close * 0.998, 10 + si + ti))
            labels[(timestamp, symbol)] = future
            baseline[(timestamp, symbol)] = float(si)
    panel = pd.DataFrame(rows, columns=["open_time", "symbol", "available_time", "close", "high", "low", "volume"])
    panel["open"] = panel["close"]
    panel = panel.set_index(["open_time", "symbol"]).sort_index()
    label_series = pd.Series(labels, dtype=float)
    label_series.index = pd.MultiIndex.from_tuples(label_series.index, names=["open_time", "symbol"])
    baseline_series = pd.Series(baseline, dtype=float)
    baseline_series.index = pd.MultiIndex.from_tuples(baseline_series.index, names=["open_time", "symbol"])
    return panel, label_series, baseline_series


def _frames(panel, request):
    from alpha_lab_service.data_feed import build_asof_frame

    steps = []
    for timestamp in TIMES:
        if pd.Timestamp(request["start"]) <= timestamp <= pd.Timestamp(request["end"]):
            history = build_asof_frame(panel, as_of=timestamp, symbols=SYMBOLS,
                                       required_columns=["open", "high", "low", "close", "volume"])
            records = history.reset_index()
            records["open_time"] = records["open_time"].map(lambda value: value.isoformat())
            records["available_time"] = records["available_time"].map(lambda value: value.isoformat())
            steps.append({"type": "step", "as_of": timestamp.isoformat(), "history": records.to_dict("records")})
    return steps


def _request(source):
    return {
        "source": source,
        "hypothesis": "Synthetic causal feature diagnostic",
        "symbols": SYMBOLS,
        "timeframe": "1d",
        "start": TIMES[0].isoformat(),
        "end": TIMES[-1].isoformat(),
        "params": {"lookback": 3},
        "max_lookback": 36,
    }


def test_momentum_and_wavelet_are_causal_and_reproducible_descriptive_runs(tmp_path):
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must identify the locally built immutable Worker image")
    panel, labels, baseline = _synthetic_panel()
    broker = WorkerBroker(WorkerProfile(image=IMAGE))
    for fixture in ("momentum.py", "fft_window.py"):
        source = (FIXTURES / fixture).read_text(encoding="utf-8")
        request = _request(source)
        steps = _frames(panel, request)
        assert all(not any("label" in key or "target" in key for key in row) for step in steps for row in step["history"])
        frames = [{
            "type": "init", "protocol_version": "1.0", "source": source,
            "symbols": SYMBOLS, "params": request["params"], "seed": 0,
        }, *steps, {"type": "end"}]
        first = broker.run_frames("acceptance-" + fixture.replace(".", "-"), frames)
        second = broker.run_frames("repeat-" + fixture.replace(".", "-"), frames)
        assert [row["output_hash"] for row in first[1:]] == [row["output_hash"] for row in second[1:]]
        service = ResearchService(
            ResearchStoreV1(tmp_path / fixture / "store.sqlite3"), broker,
            frame_provider=lambda _request, rows=steps: rows,
            labels_provider=lambda _request, index: labels.reindex(index),
            baseline_provider=lambda _request, index: baseline.reindex(index),
            data_fingerprint="synthetic-panel-sha256", image_digest=IMAGE,
            engine_identity="alpha-lab-descriptive-v1",
        )
        submitted = service.submit_factor(request)
        result = asyncio.run(service.run_factor(submitted["task_id"]))
        assert result["status"] == "EXPLORATORY", result
        assert result["report"]["metrics"]["mean_cs_ic"] is not None
        assert result["report"]["metrics"]["mean_cs_rank_ic"] is not None
        assert result["report"]["metrics"]["stability"]
        assert result["report"]["metrics"]["incremental"]
        assert "p_two_sided" not in str(result)
        assert "SUPPORTED" not in str(result)
        decision = service.append_decision(
            decision_id="decision-" + fixture, run_id=result["run_id"],
            decision={"status": "EXPLORATORY", "summary": "Synthetic acceptance only"},
        )
        assert decision["status"] == "EXPLORATORY"
        repeated = asyncio.run(service.run_factor(submitted["task_id"]))
        assert repeated["evidence_hash"] == result["evidence_hash"]


def test_malicious_factor_has_no_network_sensitive_mount_or_cross_task_effect(tmp_path):
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must identify the locally built immutable Worker image")
    source = (FIXTURES / "malicious.py").read_text(encoding="utf-8")
    panel, _labels, _baseline = _synthetic_panel()
    request = _request(source)
    step = _frames(panel, request)[:1]
    frames = [{"type": "init", "protocol_version": "1.0", "source": source,
               "symbols": SYMBOLS, "params": request["params"], "seed": 0}, *step, {"type": "end"}]
    result = WorkerBroker(WorkerProfile(image=IMAGE)).run_frames("malicious-attempt", frames)
    assert set(result[1]["values"].values()) == {0.0}


def test_trailing_factor_output_is_consistent_after_removing_irrelevant_older_history():
    if not IMAGE.startswith("sha256:"):
        pytest.fail("ALPHA_LAB_WORKER_IMAGE must identify the locally built immutable Worker image")
    panel, _labels, _baseline = _synthetic_panel()
    source = (FIXTURES / "momentum.py").read_text(encoding="utf-8")
    request = _request(source)
    complete = _frames(panel, request)[-1]
    history = complete["history"]
    last_times = sorted({row["open_time"] for row in history})[-4:]
    truncated = {**complete, "history": [row for row in history if row["open_time"] in last_times]}
    init = {"type": "init", "protocol_version": "1.0", "source": source,
            "symbols": SYMBOLS, "params": request["params"], "seed": 0}
    end = {"type": "end"}
    broker = WorkerBroker(WorkerProfile(image=IMAGE))
    full_result = broker.run_frames("truncate-full", [init, complete, end])[1]
    short_result = broker.run_frames("truncate-short", [init, truncated, end])[1]
    assert full_result["values"] == short_result["values"]

