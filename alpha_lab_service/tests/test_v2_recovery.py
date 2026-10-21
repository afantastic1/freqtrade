import asyncio
from dataclasses import replace

import pytest

from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.authorization_v2 import MappingResearchRequestRegistryV1
from alpha_lab_service.jobs_v2 import ResearchServiceV2
from alpha_lab_service.store_v2 import ResearchStoreV2
from alpha_lab_service.tests.test_v2_authorization import FakeDatasetBuilder, request_payload, research_request


def test_cross_sectional_v2_service_emits_cs_report(tmp_path):
    import pandas as pd
    from alpha_lab.data.build_products import DatasetBuildReportV1, DatasetBuildResultV2, EvaluatorLabelsV1, WorkerInputV2
    from alpha_lab_service.authorization_v2 import MappingResearchRequestRegistryV1

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    policy = FileCampaignPolicyRegistry().get(
        "example_campaign", "1", "041efb79ddf9b88a71ff071cbfb52f833420658ff888cf6b6d87bb5f0a7ef209"
    )
    changed_policy = replace(policy, symbols=tuple(symbols), policy_sha256="")
    changed_policy = replace(changed_policy, policy_sha256=changed_policy.sha256())

    class PolicyRegistry:
        def get(self, campaign_id, version, digest):
            assert (campaign_id, version, digest) == (
                changed_policy.campaign_id, changed_policy.policy_version, changed_policy.sha256()
            )
            return changed_policy

    raw_request = research_request() | {
        "request_id": "cs-service-test", "campaign_policy_sha256": changed_policy.sha256(),
        "symbols": symbols, "mode": "cross_sectional",
    }
    timestamps = pd.date_range("2025-06-01T00:05:00Z", periods=8, freq="5min", tz="UTC")

    class DatasetBuilder:
        def build(self, _request, *, options, universe=None):
            rows = []
            labels = []
            for stamp in timestamps:
                for rank, symbol in enumerate(symbols, 1):
                    rows.append({"open_time": stamp - pd.Timedelta(minutes=5), "close_time": stamp,
                                 "available_time": stamp, "decision_time": stamp, "open": 100.0,
                                 "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10.0,
                                 "symbol": symbol})
                    labels.append({"decision_time": stamp, "symbol": symbol, "label_value": float(rank)})
            return DatasetBuildResultV2(
                WorkerInputV2(pd.DataFrame(rows), ("available_time",)),
                EvaluatorLabelsV1(pd.DataFrame(labels)), DatasetBuildReportV1(), {"source": "synthetic"},
            )

    class CrossSectionBroker:
        async def run_chunk(self, _task, _chunk, frames):
            init = frames[0]
            output = [{"type": "ready", "protocol_version": "2.0"}]
            output.extend({"type": "result", "plan_hash": init["plan_hash"], "as_of": frame["as_of"],
                           "values": {symbol: float(rank) for rank, symbol in enumerate(symbols, 1)}}
                          for frame in frames if frame["type"] == "step")
            return output

    options = DatasetBuildOptionsV1("preserve", "futures_ohlcv", {}, {}, {})
    service_v2 = ResearchServiceV2(
        ResearchStoreV2(tmp_path / "cs" / "db.sqlite3", protected_paths=()), CrossSectionBroker(),
        research_request_registry=MappingResearchRequestRegistryV1({"cs-service-test": raw_request}),
        policy_registry=PolicyRegistry(), dataset_builder=DatasetBuilder(), dataset_options=options,
        worker_identity="sha256:" + "a" * 64, evaluator_identity="evaluator-v1", max_steps_per_chunk=8,
    )
    payload = request_payload() | {"research_request_id": "cs-service-test"}
    submitted = service_v2.submit_factor_v2(payload)
    result = asyncio.run(service_v2.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "EXPLORATORY", result
    assert result["report"]["mode"] == "cross_sectional"
    assert result["report"]["metrics"]["mean_cs_rank_ic"] == pytest.approx(1.0)
    assert result["report"]["formal_inference_status"] == "BLOCKED"


class FakeBroker:
    def __init__(self, *, fail_once=False):
        self.calls = []
        self.fail_once = fail_once

    async def run_chunk(self, task_id, chunk_id, frames):
        self.calls.append((chunk_id, frames))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("simulated broker timeout")
        init = frames[0]
        out = [{"type": "ready", "protocol_version": "2.0"}]
        state = init.get("state")
        for frame in frames:
            if frame["type"] != "step":
                continue
            values = {symbol: float(len(self.calls)) for symbol in init["symbols"]}
            out.append({"type": "result", "plan_hash": init["plan_hash"],
                        "as_of": frame["as_of"], "values": values})
            if init["execution_mode"] == "stateful":
                state = {"count": (state or {}).get("count", 0) + 1}
        if init["execution_mode"] == "stateful":
            out.append({"type": "checkpoint", "plan_hash": init["plan_hash"], "state": state or {}})
        return out


def service(tmp_path, *, broker=None):
    options = DatasetBuildOptionsV1("preserve", "futures_ohlcv", {}, {}, {})
    return ResearchServiceV2(
        ResearchStoreV2(tmp_path / "store" / "db.sqlite3", protected_paths=()),
        broker or FakeBroker(),
        research_request_registry=MappingResearchRequestRegistryV1({"example-request-001": research_request()}),
        policy_registry=FileCampaignPolicyRegistry(), dataset_builder=FakeDatasetBuilder(),
        dataset_options=options, worker_identity="sha256:" + "a" * 64,
        evaluator_identity="evaluator-v1", max_steps_per_chunk=1,
    )


def test_duplicate_submit_and_stateful_chunk_resume(tmp_path):
    broker = FakeBroker(fail_once=True)
    research = service(tmp_path, broker=broker)
    payload = request_payload() | {
        "execution_mode": "stateful", "state_schema_id": "json-object-v1", "state_schema_version": "1",
    }
    first = research.submit_factor_v2(payload)
    assert research.submit_factor_v2(payload)["task_id"] == first["task_id"]
    result = asyncio.run(research.run_factor_v2(first["task_id"]))
    assert result["status"] in {"DIAGNOSTIC", "EXPLORATORY", "NOT_EVALUABLE"}, result
    assert len(broker.calls) == 3
    # The retry of chunk zero starts from its original state; chunk one receives
    # only the state committed by the successful chunk zero attempt.
    assert broker.calls[0][1][0]["state"] is None
    assert broker.calls[1][1][0]["state"] is None
    assert broker.calls[2][1][0]["state"] == {"count": 1}
    assert research.get_task_v2(first["task_id"])["status"] == "COMPLETED"


def test_v2_evidence_binds_campaign_dataset_source_params_and_evaluator(tmp_path):
    research = service(tmp_path)
    payload = request_payload() | {"params": {"window": 7}}
    submitted = research.submit_factor_v2(payload)
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    evidence = research.store.get_evidence(submitted["request_hash"])["evidence"]
    assert result["status"] == "NOT_EVALUABLE"
    assert evidence["campaign"] == {
        "campaign_id": "example_campaign", "policy_version": "1",
        "campaign_policy_sha256": "041efb79ddf9b88a71ff071cbfb52f833420658ff888cf6b6d87bb5f0a7ef209",
    }
    assert len(evidence["research_request_sha256"]) == 64
    assert evidence["mode"] == "time_series"
    assert evidence["dataset_source_provenance"] == {"marker": "one"}
    assert evidence["source_sha256"]
    assert evidence["params"] == {"window": 7}
    assert evidence["evaluator_version"] == "evaluator-v1"
    assert evidence["worker_image_digest"] == "sha256:" + "a" * 64
    assert evidence["formal_inference_status"] == "BLOCKED"
    replay = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert replay["evidence_hash"] == result["evidence_hash"]
    changed = dict(evidence, report={"status": "changed"})
    with pytest.raises(ValueError, match="Evidence identity conflict"):
        research.store.complete_with_evidence(task_id=submitted["task_id"], run_id=submitted["request_hash"], evidence=changed)


def test_runtime_failure_is_blocked_and_never_publishes_evidence(tmp_path):
    class BrokenBroker:
        async def run_chunk(self, *_args):
            raise RuntimeError("worker runtime failed")

    research = service(tmp_path, broker=BrokenBroker())
    submitted = research.submit_factor_v2(request_payload())
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "EXECUTION_BLOCKED"
    record = research.store.get_task_record(submitted["task_id"])
    assert research.store.get_evidence(record["request_hash"]) is None
    assert research.get_task_v2(submitted["task_id"])["status"] == "EXECUTION_BLOCKED"


def test_process_restart_replays_uncommitted_chunk_from_checkpoint(tmp_path):
    class CrashOnceBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.invocations = 0

        async def run_chunk(self, *args):
            self.invocations += 1
            if self.invocations == 2:
                raise asyncio.CancelledError()
            return await super().run_chunk(*args)

    broker = CrashOnceBroker()
    research = service(tmp_path, broker=broker)
    payload = request_payload() | {
        "execution_mode": "stateful", "state_schema_id": "json-object-v1", "state_schema_version": "1",
    }
    submitted = research.submit_factor_v2(payload)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(research.run_factor_v2(submitted["task_id"]))
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] in {"DIAGNOSTIC", "EXPLORATORY", "NOT_EVALUABLE"}
    chunks = research.store.get_chunks(submitted["task_id"])
    assert research.store.get_chunk(submitted["task_id"], chunks[0]["chunk_id"])["attempts"] == 1
    assert research.store.get_chunk(submitted["task_id"], chunks[1]["chunk_id"])["attempts"] == 2
    assert broker.calls[1][1][0]["state"] == {"count": 1}


def test_wall_limit_blocks_before_worker_launch(tmp_path, monkeypatch):
    import alpha_lab_service.jobs_v2 as jobs_v2

    monkeypatch.setattr(jobs_v2, "V2_LIMITS", jobs_v2.V2_LIMITS.model_copy(
        update={"max_total_worker_wall_seconds": 0}
    ))
    broker = FakeBroker()
    research = service(tmp_path, broker=broker)
    submitted = research.submit_factor_v2(request_payload())
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "EXECUTION_BLOCKED"
    assert broker.calls == []
    assert research.store.get_evidence(submitted["request_hash"]) is None


def test_retry_limit_is_three_and_exhaustion_publishes_no_evidence(tmp_path):
    class AlwaysTimeoutBroker:
        def __init__(self):
            self.calls = 0

        async def run_chunk(self, *_args):
            self.calls += 1
            raise TimeoutError("broker timeout")

    broker = AlwaysTimeoutBroker()
    research = service(tmp_path, broker=broker)
    submitted = research.submit_factor_v2(request_payload())
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "FAILED"
    assert broker.calls == 3
    assert research.store.get_evidence(submitted["request_hash"]) is None


def test_checkpoint_commit_failure_never_publishes_evidence(tmp_path, monkeypatch):
    research = service(tmp_path)
    submitted = research.submit_factor_v2(request_payload() | {
        "execution_mode": "stateful", "state_schema_id": "json-object-v1", "state_schema_version": "1",
    })

    def fail_commit(*_args, **_kwargs):
        raise OSError("simulated checkpoint transaction failure")

    monkeypatch.setattr(research.store, "commit_chunk", fail_commit)
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "EXECUTION_BLOCKED"
    assert research.store.get_evidence(submitted["request_hash"]) is None


def test_evaluator_failure_never_publishes_evidence(tmp_path, monkeypatch):
    import alpha_lab_service.jobs_v2 as jobs_v2

    research = service(tmp_path)
    submitted = research.submit_factor_v2(request_payload())

    def fail_evaluation(*_args, **_kwargs):
        raise RuntimeError("simulated evaluator failure")

    monkeypatch.setattr(jobs_v2, "evaluate_authorized_factor_output", fail_evaluation)
    result = asyncio.run(research.run_factor_v2(submitted["task_id"]))
    assert result["status"] == "FAILED"
    assert research.store.get_evidence(submitted["request_hash"]) is None
