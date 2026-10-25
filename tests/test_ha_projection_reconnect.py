"""Projection failures belong only to the WebSocket that sent the request."""

import asyncio
import copy
from contextlib import asynccontextmanager

import aiohttp

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.clock import SystemClock
from dev.fake_ha import FakeHA

from .conftest import activate
from .test_declarative import definition, registry
from .test_ha_request_ids import until


async def exercise_reconnect(runtime, config, aiohttp_server, *, revision_change, monkeypatch):
    config = registry(config, definition())
    config["contracts"][0]["ha_projection"] = True
    await activate(runtime, config)
    now = runtime.clock.now_utc().isoformat()
    state = {"state": True, "attributes": {}, "last_updated": now, "last_reported": now}
    fake = FakeHA({"sensor.example_1": state, "sensor.example_2": state}, v2=True, projection=True)
    fake.projection_ack = False
    server = await aiohttp_server(fake.application())
    snapshots = []
    timeout_triggered = asyncio.Event()
    original_timeout = asyncio.timeout

    @asynccontextmanager
    async def controlled_timeout(seconds):
        if seconds == 20 and not revision_change and not timeout_triggered.is_set():
            # Exercise the real timeout exception without waiting twenty wall-clock seconds.
            timeout_triggered.set()
            fake.projection_ack = True
            async with original_timeout(0):
                yield
        else:
            async with original_timeout(seconds):
                yield

    if not revision_change:
        monkeypatch.setattr(asyncio, "timeout", controlled_timeout)

    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        original_event = adapter.event

        async def observe_snapshot(event, config, revision=None):
            await original_event(event, config, revision)
            if event["kind"] == "snapshot":
                snapshots.append((len(fake.connections), revision))

        adapter.event = observe_snapshot
        tasks = [
            asyncio.create_task(adapter.run()),
            asyncio.create_task(adapter.projection_worker()),
        ]
        try:
            if revision_change:
                await until(
                    lambda: (
                        runtime.first_snapshot
                        and adapter.connection is not None
                        and bool(adapter.connection.pending)
                    )
                )
                old_connection = adapter.connection
                old_futures = list(old_connection.pending.values())
                changed = copy.deepcopy(config)
                changed["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.example_2"
                fake.projection_ack = True
                await activate(runtime, changed)
            else:
                await timeout_triggered.wait()
            await until(
                lambda: (
                    len(fake.connections) >= 2
                    and (2, runtime.state.active_revision) in snapshots
                    and runtime.ready
                    and adapter.connection is not None
                    and not adapter.connection.pending
                )
            )
            assert (
                runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True
            )
            # A new publication must also finish on the same replacement connection.
            previous = len(fake.requests)
            entity = "sensor.example_2" if revision_change else "sensor.example_1"
            await fake.emit(
                {
                    "kind": "changed",
                    "entity_id": entity,
                    "new_state": {**state, "state": False},
                    "time_fired": now,
                }
            )
            await until(
                lambda: (
                    runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"]
                    is False
                    and len(fake.requests) > previous
                    and not adapter.connection.pending
                )
            )
            await SystemClock().sleep(0)
            assert len(fake.connections) == 2
            assert runtime.first_snapshot and runtime.service_state()["ready"]
            assert "first_bridge_snapshot_pending" not in runtime.readiness_reasons
            assert not runtime.resubscribe.is_set()
            assert all(not task.done() for task in tasks)
            if revision_change:
                assert adapter.connection is not old_connection
                assert not old_connection.pending
                assert all(future.done() for future in old_futures)
                subscribed = next(r for r in fake.connections[1] if r["type"] == "traber/subscribe")
                assert subscribed["entity_ids"] == ["sensor.example_2"]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert adapter.connection is None


async def test_revision_with_pending_projection_reconnects_once_and_processes_snapshot(
    runtime, config, aiohttp_server, monkeypatch
):
    await exercise_reconnect(
        runtime, config, aiohttp_server, revision_change=True, monkeypatch=monkeypatch
    )


async def test_current_projection_timeout_still_reconnects_once(
    runtime, config, aiohttp_server, monkeypatch
):
    await exercise_reconnect(
        runtime, config, aiohttp_server, revision_change=False, monkeypatch=monkeypatch
    )
