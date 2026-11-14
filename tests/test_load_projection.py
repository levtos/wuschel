"""Projection pause and throttling on a real synthetic HA WebSocket."""

import asyncio
from uuid import uuid4

import aiohttp

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.runtime import Runtime
from dev.fake_ha import FakeHA

from .conftest import activate
from .test_beta5_projection import Projection
from .test_declarative import definition, observation, registry
from .test_ha_request_ids import until


async def settle():
    for _ in range(30):
        await asyncio.sleep(0)  # noqa: TID251


async def test_pause_stops_heartbeats_keeps_truth_and_stales_traber_after_120s(
    runtime, config, aiohttp_server
):
    config = registry(config, definition())
    config["contracts"][0]["ha_projection"] = True
    runtime.installation_id = str(uuid4())
    await activate(runtime, config)
    now = runtime.clock.now_utc().isoformat()
    fake = FakeHA(
        {
            "sensor.example_1": {
                "state": True,
                "attributes": {},
                "last_updated": now,
                "last_reported": now,
            }
        },
        v2=True,
        projection=True,
    )
    fake.projection_cache = Projection(now=runtime.clock.monotonic)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        tasks = [
            asyncio.create_task(adapter.run()),
            asyncio.create_task(adapter.projection_worker()),
        ]
        try:
            await until(
                lambda: fake.projection_cache.rows.get("fixture.echo", {}).get("value") is True
            )
            runtime.set_projection_paused(True)
            await settle()
            sent = len([r for r in fake.requests if r["type"] == "traber/project_contracts"])
            before = runtime.state.publication_seq
            await runtime.submit("observation", observation(runtime, False))
            assert runtime.state.publication_seq > before
            assert (
                runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is False
            )
            runtime.clock.advance(119)
            await settle()
            assert fake.projection_cache.fresh
            runtime.clock.advance(1)
            await settle()
            assert not fake.projection_cache.fresh and fake.projection_cache.available
            assert (
                len([r for r in fake.requests if r["type"] == "traber/project_contracts"]) == sent
            )
            assert len(fake.connections) == 1
            assert not runtime.resubscribe.is_set()
            runtime.set_projection_paused(False)
            await until(
                lambda: (
                    fake.projection_cache.fresh
                    and fake.projection_cache.rows["fixture.echo"]["value"] is False
                )
            )
            assert fake.projection_cache.sequence == runtime.state.publication_seq
            # Pause is deliberately absent from the stored state / a new process.
            fresh = Runtime(runtime.store, runtime.clock, runtime.types)
            assert fresh.projection_paused is False
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_protection_coalesces_at_configured_interval_without_reconnect(
    runtime, config, aiohttp_server
):
    config = registry(config, definition())
    config["contracts"][0]["ha_projection"] = True
    runtime.installation_id = str(uuid4())
    await activate(runtime, config)
    now = runtime.clock.now_utc().isoformat()
    fake = FakeHA(
        {
            "sensor.example_1": {
                "state": False,
                "attributes": {},
                "last_updated": now,
                "last_reported": now,
            }
        },
        v2=True,
        projection=True,
    )
    fake.projection_cache = Projection(now=runtime.clock.monotonic)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        tasks = [
            asyncio.create_task(adapter.run()),
            asyncio.create_task(adapter.projection_worker()),
        ]
        try:
            await until(
                lambda: fake.projection_cache.rows.get("fixture.echo", {}).get("value") is False
            )
            runtime.event_loop_lag_ms = 1000
            runtime.sample_load()
            runtime.clock.advance(runtime.operations.load_enter_hold_s)
            runtime.sample_load()
            assert runtime.load_guard.state == "geschützt"
            await settle()

            def projections():
                return [r for r in fake.requests if r["type"] == "traber/project_contracts"]

            baseline = len(projections())
            for index in range(51):
                await runtime.submit("observation", observation(runtime, bool(index % 2)))
            await runtime.submit("observation", observation(runtime, True))
            await settle()
            assert len(projections()) == baseline
            # Last normal send was at zero; protected sends cannot bypass the gate.
            runtime.clock.advance(4.9)
            await settle()
            assert len(projections()) == baseline
            runtime.clock.advance(0.1)
            await until(lambda: len(projections()) == baseline + 1)
            assert projections()[-1]["publication_seq"] == runtime.state.publication_seq
            assert projections()[-1]["contracts"][0]["value"] is True
            assert len(fake.connections) == 1 and not runtime.resubscribe.is_set()
            # Recovery lifts the gate immediately after its own hold.
            runtime.event_loop_lag_ms = 0
            runtime.sample_load()
            runtime.clock.advance(runtime.operations.load_recover_hold_s)
            runtime.sample_load()
            assert runtime.load_guard.state == "normal"
            await runtime.submit("observation", observation(runtime, False))
            await until(lambda: fake.projection_cache.rows["fixture.echo"]["value"] is False)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
