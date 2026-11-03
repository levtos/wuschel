"""Synthetic concurrency checks: workers never own the live writer."""

import asyncio
import threading

import pytest

from core_contracts.management import Planner

from .conftest import activate
from .test_runtime import obs


async def test_blocked_validation_does_not_block_live_input(runtime, config, monkeypatch):
    await activate(runtime, config)
    entered, release = threading.Event(), threading.Event()
    original = Planner.process

    async def slow(self, operation, payload):
        if operation == "validate":
            entered.set()
            assert release.wait(10)
        return await original(self, operation, payload)

    monkeypatch.setattr(Planner, "process", slow)
    request = asyncio.create_task(runtime.submit("validate", config))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for index in range(50):
            runtime.clock.advance(0.02)
            await runtime.submit("observation", obs(runtime, bool(index % 2)))
        assert not request.done()
        assert not runtime.resubscribe.is_set()
        assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True
    finally:
        release.set()
        await request


async def test_prepared_activation_rejects_changed_live_basis(runtime, config):
    from core_contracts.management import capture, commit, prepare

    await activate(runtime, config)
    plan = capture(runtime)
    plan.operation = "rollback"
    plan = await asyncio.to_thread(
        prepare,
        runtime,
        plan,
        {"revision": 1, "expected_active_revision": 1},
        asyncio.get_running_loop(),
    )
    await runtime.submit("observation", obs(runtime))
    before = runtime.state.model_dump_json()
    with pytest.raises(ValueError, match="active_revision_conflict"):
        await commit(runtime, plan)
    assert runtime.state.model_dump_json() == before


async def test_overflow_coalesces_without_reconnecting_and_keeps_order(runtime, config):
    await activate(runtime, config)
    original_queue = runtime.queue
    runtime.queue = asyncio.Queue(1)
    first = obs(runtime, False)
    runtime.ingest(first)
    runtime.clock.advance(1)
    runtime.ingest(obs(runtime, True))
    runtime.clock.advance(1)
    newest = obs(runtime, False)
    runtime.ingest(newest)
    assert runtime.latest == {"binding.a": newest}
    assert not runtime.resubscribe.is_set()
    queued = runtime.queue.get_nowait()
    assert queued.payload is first
    await runtime.process(queued.operation, queued.payload)
    runtime.queue.task_done()
    await runtime._drain_overflow()
    assert (
        runtime.state.tables["source_observation_current"]["binding.a"]
        == newest.model_copy(update={"observation_kind": "snapshot"}).storage_document()
    )
    assert not runtime.latest
    runtime.queue = original_queue
