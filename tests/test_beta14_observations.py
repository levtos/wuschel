"""Revision-local evidence never acquires a new measurement time or crosses a gap."""

import asyncio
from copy import deepcopy

import aiohttp

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.registry import storage_document
from core_contracts.runtime import Runtime
from dev.fake_ha import FakeHA

from .conftest import activate
from .test_beta_openings import with_connectivity
from .test_declarative import observation
from .test_ha_request_ids import until


def heartbeat(config):
    config["fingerprint_version"] = 2
    config["sources"][0]["freshness"] = dict(
        mode="report_heartbeat", interval_s=300, future_tolerance_s=0
    )
    return config


def field(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]


def unrelated(runtime):
    document = storage_document(runtime.config)
    document["contracts"].append({**document["contracts"][0], "contract_id": "fixture.independent"})
    return document


async def test_unrelated_activation_keeps_socket_observation_age_and_new_binding_waits_for_report(
    runtime, config, aiohttp_server
):
    await activate(runtime, with_connectivity(heartbeat(config)))
    stamp = runtime.clock.now_utc().isoformat()
    state = dict(
        state=True, attributes={"alternate": False}, last_reported=stamp, last_updated=stamp
    )
    fake = FakeHA({"sensor.example_1": state}, v2=True)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        task = asyncio.create_task(adapter.run())
        try:
            await until(lambda: runtime.first_snapshot and adapter.socket is not None)
            before = deepcopy(runtime.state.tables["source_observation_current"]["binding.a"])
            socket = adapter.socket
            runtime.clock.advance(60)
            await activate(runtime, unrelated(runtime))
            await until(lambda: not runtime.revision_changed.is_set())
            assert adapter.socket is socket and len(fake.connections) == 1
            assert runtime.state.tables["source_observation_current"]["binding.a"] == before
            assert field(runtime)["value"] is True and field(runtime)["status"] == "valid"
            runtime.clock.advance(239)
            await runtime.submit("tick")
            assert field(runtime)["status"] == "valid"
            runtime.clock.advance(2)
            await runtime.submit("tick")
            assert field(runtime)["status"] == "unknown"
            assert field(runtime)["reasons"][0]["code"] == "input_stale"

            document = storage_document(runtime.config)
            document["bindings"][0]["adapter"].update(kind="ha_attribute", ha_attribute="alternate")
            await activate(runtime, document)
            await until(lambda: not runtime.revision_changed.is_set())
            assert adapter.socket is socket
            assert "binding.a" not in runtime.state.tables["source_observation_current"]
            assert field(runtime)["status"] == "unknown"
            runtime.clock.advance(1)
            await fake.emit(
                {
                    "kind": "reported",
                    "entity_id": "sensor.example_1",
                    "last_reported": runtime.clock.now_utc().isoformat(),
                }
            )
            await until(
                lambda: field(runtime)["status"] == "valid" and field(runtime)["value"] is False
            )
            assert (
                runtime.state.tables["source_observation_current"]["binding.a"]["registry_revision"]
                == runtime.state.active_revision
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_activation_during_known_ha_gap_does_not_keep_live_evidence(runtime, config):
    await activate(runtime, heartbeat(config))
    await runtime.submit("bridge_state", ("connected", "connected"))
    await runtime.submit("observation", observation(runtime, True))
    runtime.clock.advance(1)
    document = unrelated(runtime)
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    # Existing disconnect semantics age the original evidence normally.
    assert field(runtime)["status"] == "valid"
    await activate(runtime, document)
    assert field(runtime)["status"] == "unknown"
    assert field(runtime)["reasons"][0]["code"] == "input_restored"
    await runtime.submit("bridge_state", ("connected", "connected"))
    assert field(runtime)["status"] == "unknown"
    runtime.clock.advance(1)
    await runtime.submit("observation", observation(runtime, True))
    assert field(runtime)["status"] == "valid"


async def test_process_restart_still_requires_fresh_evidence(runtime, config):
    await activate(runtime, heartbeat(config))
    await runtime.submit("observation", observation(runtime, True))
    assert field(runtime)["status"] == "valid"
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        assert field(restarted)["status"] == "unknown"
        assert field(restarted)["reasons"][0]["code"] == "input_restored"
        restarted.clock.advance(1)
        await restarted.submit("observation", observation(restarted, True))
        assert field(restarted)["status"] == "valid"
    finally:
        await restarted.stop()
