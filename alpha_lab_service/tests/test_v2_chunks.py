from types import SimpleNamespace
from dataclasses import replace

import pandas as pd
import pytest

from alpha_lab.data.build_products import DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2
from alpha_lab_contracts.research_request_v2 import ResearchRequestV2
from alpha_lab_service.authorization_v2 import AuthorizedFactorRunV2
from alpha_lab_service.chunking_v2 import ChunkV2, build_chunk_frames, plan_chunks, validate_chunk_output
from alpha_lab_service.input_schemas_v2 import INPUT_SCHEMA_REGISTRY
from alpha_lab_service.protocol_v2 import FactorRequestV2, V2_LIMITS


def make_run(count=513, *, lookback=2, byte_bomb=False, future_row=False):
    start = pd.Timestamp("2025-06-01T00:00:00Z")
    timestamps = pd.date_range(start, periods=count, freq="5min", tz="UTC")
    frame = pd.DataFrame({
        "open_time": timestamps - pd.Timedelta(minutes=5),
        "close_time": timestamps,
        "available_time": timestamps,
        "decision_time": timestamps,
        "open": [1.0] * count, "high": [2.0] * count, "low": [0.5] * count,
        "close": [1.5] * count, "volume": [10.0] * count,
        "symbol": ["BTCUSDT"] * count,
    })
    if byte_bomb:
        frame["symbol"] = "B" * 40_000
    if future_row:
        frame.loc[0, "open_time"] = timestamps[1] + pd.Timedelta(minutes=5)
    worker = WorkerInputV2(frame, ("available_time",))
    labels = EvaluatorLabelsV1(pd.DataFrame({"decision_time": timestamps, "label_value": [1.0] * count}))
    request_raw = {
        "protocol_version": "2.0-research-request", "request_id": "chunk-test",
        "campaign_id": "example_campaign", "policy_version": "1",
        "campaign_policy_sha256": "041efb79ddf9b88a71ff071cbfb52f833420658ff888cf6b6d87bb5f0a7ef209",
        "start": start.isoformat(), "end": (timestamps[-1] + pd.Timedelta(minutes=5)).isoformat(),
        "symbols": ["BTCUSDT"], "timeframe": "5m", "mode": "time_series",
        "data_types": ["futures_ohlcv"], "split": "holdout", "lookback_bars": lookback,
        "label_horizon_bars": 1, "embargo_bars": 0,
    }
    request = ResearchRequestV2.from_dict(request_raw, _PolicyRegistry())
    factor = FactorRequestV2(
        research_request_id="chunk-test", source="def factor(frame, params): return 1.0",
        worker_input_schema_id="ohlcv-v1", worker_input_schema_version="1", execution_mode="stateless",
    )
    dataset = DatasetBuildResultV2(worker, labels, DatasetBuildReportV1(), {"test": True})
    return AuthorizedFactorRunV2(
        research_request=request, campaign_policy_sha256=request.campaign_policy_sha256,
        dataset=dataset, input_schema=INPUT_SCHEMA_REGISTRY.get("ohlcv-v1", "1"),
        evaluation_timestamps=tuple(timestamps), identity_hash="a" * 64,
        canonical_request_record={"factor_request": factor.model_dump(mode="json")},
        worker_image_digest="sha256:" + "b" * 64, evaluator_identity="eval-v1",
    )


class _PolicyRegistry:
    def get(self, campaign_id, policy_version, policy_sha256):
        from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
        return FileCampaignPolicyRegistry().get(campaign_id, policy_version, policy_sha256)


def test_month_window_has_deterministic_non_overlapping_chunks():
    run = make_run(8_928)
    first, second = plan_chunks(run), plan_chunks(run)
    assert first.plan_hash == second.plan_hash
    assert len(first.chunks) == 18
    assert all(len(chunk.evaluation_timestamps) <= 512 for chunk in first.chunks)
    merged = tuple(timestamp for chunk in first.chunks for timestamp in chunk.evaluation_timestamps)
    assert merged == run.evaluation_timestamps
    assert len(set(merged)) == 8_928
    assert first.total_input_bytes <= V2_LIMITS.max_total_input_bytes


@pytest.mark.parametrize(("count", "chunk_sizes"), [(512, [512]), (513, [512, 1])])
def test_512_step_boundary_is_enforced(count, chunk_sizes):
    plan = plan_chunks(make_run(count))
    assert [len(chunk.evaluation_timestamps) for chunk in plan.chunks] == chunk_sizes


def test_chunk_frames_use_point_in_time_history_and_read_only_warmup():
    run = make_run(513)
    plan = plan_chunks(run)
    first_frames = build_chunk_frames(run, plan.chunks[0])
    second_frames = build_chunk_frames(run, plan.chunks[1])
    assert first_frames[1]["type"] == "warmup"
    assert first_frames[1]["phase"] == "initialize"
    assert first_frames[0]["state"] is None
    assert second_frames[1]["phase"] == "read_only"
    assert not any(frame["type"] == "result" for frame in first_frames)
    assert all(row["available_time"] <= first_frames[2]["as_of"] for row in first_frames[2]["history"])
    assert all(row["close"] is not None for row in first_frames[2]["history"])
    assert len(first_frames[2]["history"]) <= 2


def test_single_symbol_time_series_restores_trusted_symbol_on_wire():
    run = make_run(count=2)
    worker = WorkerInputV2(run.dataset.worker_input.frame.drop(columns=["symbol"]), ("available_time",))
    run = replace(run, dataset=replace(run.dataset, worker_input=worker))
    frames = build_chunk_frames(run, plan_chunks(run).chunks[0])
    assert all(row["symbol"] == "BTCUSDT" for row in frames[2]["history"])


def test_chunk_builder_rejects_serialized_input_over_per_chunk_limit():
    run = make_run(512, lookback=500, byte_bomb=True)
    symbol = "B" * 40_000
    request = SimpleNamespace(
        start=run.research_request.start, end=run.research_request.end,
        lookback_bars=500, symbols=(symbol,),
    )
    run = replace(run, research_request=request)
    chunk = ChunkV2("oversized", 0, "a" * 64, run.evaluation_timestamps, ())
    with pytest.raises(ValueError, match="input.*byte|byte.*limit"):
        build_chunk_frames(run, chunk)


def test_chunk_builder_rejects_future_rows_and_labels():
    run = make_run(2, future_row=True)
    with pytest.raises(ValueError, match="future|as.of|available"):
        build_chunk_frames(run, plan_chunks(run).chunks[0])
    run = make_run(2)
    run.dataset.worker_input._frame["label_value"] = 1.0
    with pytest.raises(ValueError, match="label"):
        build_chunk_frames(run, plan_chunks(run).chunks[0])


def test_chunk_input_materializes_one_trusted_asof_snapshot_per_chunk():
    run = make_run(32)
    chunk = ChunkV2("chunk", 0, "a" * 64, run.evaluation_timestamps, ())
    calls = 0
    original = run.dataset.worker_input.asof

    def counted_asof(timestamp):
        nonlocal calls
        calls += 1
        return original(timestamp)

    object.__setattr__(run.dataset.worker_input, "asof", counted_asof)
    build_chunk_frames(run, chunk)
    assert calls <= 1


def test_output_validation_rejects_missing_duplicate_reordered_and_extra_results():
    run = make_run(3)
    chunk = plan_chunks(run).chunks[0]
    frames = build_chunk_frames(run, chunk)
    ready = {"type": "ready", "protocol_version": "2.0"}
    good = [{"type": "result", "plan_hash": chunk.plan_hash, "as_of": stamp.isoformat(), "values": {"BTCUSDT": 1.0}}
            for stamp in chunk.evaluation_timestamps]
    assert validate_chunk_output(run, chunk, frames, [ready, *good]) == good
    for output in ([ready, *good[:-1]], [ready, good[0], good[0], good[2]],
                   [ready, *reversed(good)], [ready, *good, good[0]]):
        with pytest.raises(ValueError):
            validate_chunk_output(run, chunk, frames, output)


@pytest.mark.parametrize("partition", [37, 128, 512])
def test_stateless_chunk_output_matches_sequential_reference(partition):
    run = make_run(513)
    chunks = plan_chunks(run, max_steps_per_chunk=partition).chunks
    merged = []
    for chunk in chunks:
        frames = build_chunk_frames(run, chunk)
        output = [{"type": "ready", "protocol_version": "2.0"}]
        output.extend({"type": "result", "plan_hash": chunk.plan_hash, "as_of": stamp.isoformat(),
                       "values": {"BTCUSDT": float((stamp - run.evaluation_timestamps[0]).total_seconds() / 300)}}
                      for stamp in chunk.evaluation_timestamps)
        merged.extend(validate_chunk_output(run, chunk, frames, output))
    reference = [{"as_of": stamp.isoformat(), "values": {"BTCUSDT": float(index)}}
                 for index, stamp in enumerate(run.evaluation_timestamps)]
    assert [{"as_of": row["as_of"], "values": row["values"]} for row in merged] == reference
