import asyncio

import pytest

from alpha_lab.data.temporal_plan import DatasetBuildOptionsV1
from alpha_lab_contracts.campaign_policy_v1 import FileCampaignPolicyRegistry
from alpha_lab_service.authorization_v2 import MappingResearchRequestRegistryV1
from alpha_lab_service.jobs_v2 import ResearchServiceV2
from alpha_lab_service.store_v2 import ResearchStoreV2
from alpha_lab_service.tests.test_v2_authorization import FakeDatasetBuilder, request_payload, research_request


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
    assert result["status"] in {"DIAGNOSTIC", "EXPLORATORY"}, result
    assert len(broker.calls) == 3
    # The retry of chunk zero starts from its original state; chunk one receives
    # only the state committed by the successful chunk zero attempt.
    assert broker.calls[0][1][0]["state"] is None
    assert broker.calls[1][1][0]["state"] is None
    assert broker.calls[2][1][0]["state"] == {"count": 1}
    assert research.get_task_v2(first["task_id"])["status"] == "COMPLETED"


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
    assert result["status"] in {"DIAGNOSTIC", "EXPLORATORY"}
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
