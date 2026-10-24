"""Synthetic projection boundary: no HA addresses or household data."""

import asyncio
import copy
import importlib.util
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.adapters.ha import BridgeAdapter, WebSocketRequests
from core_contracts.clock import SystemClock
from core_contracts.ha_projection import snapshot
from core_contracts.registry import fingerprints
from tests.conftest import activate
from tests.test_beta_openings import NOW, closed, configuration, make_runtime
from tests.test_declarative import definition
from tests.test_declarative import registry as declarative_registry

spec = importlib.util.spec_from_file_location(
    "traber_projection", Path(__file__).parents[1] / "custom_components/traber/projection.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Projection = module.Projection


def payload(**changes):
    return {
        "installation_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "publication_seq": 3,
        "contracts": [
            {
                "contract_id": "test.scalar",
                "name": "Fixture",
                "value": "on",
                "status": "valid",
                "quality": "healthy",
                "since_at": NOW.isoformat(),
                "unit": None,
            }
        ],
        **changes,
    }


def test_transport_order_owner_removal_and_reconnect():
    projection, owner = Projection(), object()
    data = payload()
    assert projection.accept(owner, data["installation_id"], data)
    with pytest.raises(ValueError, match="connection_conflict"):
        projection.accept(object(), data["installation_id"], data)
    assert not projection.accept(owner, data["installation_id"], {**data, "publication_seq": 2})
    projection.disconnect(object())
    assert projection.connected
    projection.disconnect(owner)
    assert not projection.connected
    assert projection.accept(
        object(), data["installation_id"], {**data, "publication_seq": 4, "contracts": []}
    )
    assert projection.rows == {}


@pytest.mark.parametrize(
    "change",
    [
        {"publication_seq": True},
        {"epoch_id": "bad"},
        {"contracts": [{}]},
        {"contracts": [payload()["contracts"][0]] * 257},
    ],
)
def test_invalid_transport_is_atomic(change):
    projection, owner = Projection(), object()
    data = payload()
    projection.accept(owner, data["installation_id"], data)
    previous = copy.deepcopy(projection.rows)
    with pytest.raises((ValueError, TypeError)):
        projection.accept(owner, data["installation_id"], {**data, **change})
    assert projection.rows == previous


async def test_only_opted_in_confirmed_scalar_values_and_fingerprint_neutrality():
    runtime = await make_runtime()
    assert snapshot(runtime)["contracts"] == []
    before = fingerprints(runtime.config, runtime.types)
    document = configuration()
    document["contracts"][0]["ha_projection"] = True
    await activate(runtime, document)
    await closed(runtime, 0)
    assert fingerprints(runtime.config, runtime.types) == before
    (row,) = snapshot(runtime)["contracts"]
    assert row["contract_id"] == "test.opening0" and row["value"] == "closed"
    runtime.confirmed = False
    (row,) = snapshot(runtime)["contracts"]
    assert row["status"] == "unknown" and row["value"] is None


@pytest.mark.parametrize("kind", ["snapshot", "live_change", "report"])
async def test_ha_fixture_with_real_transport_timestamps_projects_published_value(
    runtime, config, kind
):
    config = declarative_registry(config, definition())
    config["contracts"][0]["ha_projection"] = True
    await activate(runtime, config)
    await runtime.submit("bridge_state", ("connected", "connected"))
    adapter = BridgeAdapter(runtime, None, "", "")
    stamps = {
        name: (NOW - timedelta(seconds=seconds)).isoformat()
        for name, seconds in [("last_changed", 30), ("last_updated", 20), ("last_reported", 10)]
    }
    state = {"state": "on", "attributes": {}, **stamps}
    event = {"time_fired": NOW.isoformat(), "last_reported": stamps["last_reported"]}
    observation = adapter.observation(
        runtime.config.bindings[0], state, kind, event, runtime.config
    )
    assert observation.ha_last_changed.isoformat() == stamps["last_changed"]
    assert observation.ha_last_updated.isoformat() == stamps["last_updated"]
    assert observation.ha_last_reported.isoformat() == stamps["last_reported"]
    assert observation.ha_time_fired == NOW
    expected = NOW if kind == "live_change" else NOW - timedelta(seconds=10)
    assert observation.measurement == expected
    await runtime.submit("observation", observation)
    (row,) = snapshot(runtime)["contracts"]
    assert (
        row["contract_id"] == "fixture.echo" and row["status"] == "valid" and row["value"] is True
    )
    assert row["since_at"] is not None
    # A subsequent unknown state must not project the last usable value.
    unknown = adapter.observation(
        runtime.config.bindings[0],
        {**state, "state": "unknown"},
        "live_change",
        event,
        runtime.config,
    )
    await runtime.submit("observation", unknown)
    (row,) = snapshot(runtime)["contracts"]
    assert row["status"] == "unknown" and row["value"] is None


async def test_missing_measurement_never_becomes_a_positive_projection(runtime, config):
    config = declarative_registry(config, definition())
    config["contracts"][0]["ha_projection"] = True
    await activate(runtime, config)
    await runtime.submit("bridge_state", ("connected", "connected"))
    adapter = BridgeAdapter(runtime, None, "", "")
    observation = adapter.observation(
        runtime.config.bindings[0],
        {"state": "on", "attributes": {}},
        "live_change",
        {},
        runtime.config,
    )
    assert observation.measurement is None
    await runtime.submit("observation", observation)
    (row,) = snapshot(runtime)["contracts"]
    assert row["status"] == "unknown" and row["value"] is None


async def test_projection_worker_initial_send_update_ack_and_disconnect():
    runtime = await make_runtime()
    document = configuration()
    document["contracts"][0]["ha_projection"] = True
    await activate(runtime, document)
    sent = asyncio.Queue()

    class Socket:
        async def send_json(self, message):
            await sent.put(message)

    adapter = BridgeAdapter(runtime, None, "", "")
    connection = WebSocketRequests(Socket())
    adapter.connection = connection
    adapter.projection_supported = True
    adapter.projection_connected.set()
    worker = asyncio.create_task(adapter.projection_worker())
    try:
        async with asyncio.timeout(2):
            message = await sent.get()
            assert message["contracts"][0]["value"] is None
            connection.pending[message["id"]].set_result({"applied": True})
            await closed(runtime, 0)
            message = await sent.get()
            assert message["contracts"][0]["value"] == "closed"
            connection.pending[message["id"]].set_result({"applied": True})
            adapter.connection = None
            adapter.projection_connected.clear()
            adapter.projection_disconnected.set()
            await SystemClock().sleep(0)
            assert sent.empty()
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert not connection.pending
