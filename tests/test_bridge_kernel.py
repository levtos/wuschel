"""Actual HA state engine + event helpers + our decorated Bridge callback.

Runs in the dedicated HA CI job. This is not a Supervisor installation test.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytest.importorskip("homeassistant")
from homeassistant import loader
from homeassistant.components import websocket_api
from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import Context, HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.setup import async_setup_component

from custom_components.traber import async_setup_entry
from custom_components.traber.service_channel import Rejected
from custom_components.traber.source_metadata import refresh_action, source_metadata


@pytest.mark.ha
async def test_reverse_command_service_uses_real_ha_service_and_admin_session(
    tmp_path, monkeypatch
):
    hass = kernel(tmp_path)
    commands, results, events, errors, unload = {}, {}, [], [], []
    arrived = asyncio.Event()

    def receive(identifier, event):
        events.append(event)
        arrived.set()

    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    await async_setup_entry(hass, SimpleNamespace(async_on_unload=unload.append))
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda i, result=None: results.update({i: result}),
        send_event=receive,
        send_error=lambda *args: errors.append(args),
    )
    installation = str(uuid4())
    commands["command_subscribe"](hass, connection, {"id": 1, "installation_id": installation})
    assert errors[-1][1] == "command_session_required"
    commands["negotiate"](
        hass, connection, {"id": 2, "supported_versions": [2], "installation_id": installation}
    )
    assert "command_ingress_v1" in results[2]["features"]
    commands["command_subscribe"](hass, connection, {"id": 3, "installation_id": installation})
    data = {"contract_id": "fixture.sleep", "command": "confirm_sleep"}
    task = asyncio.create_task(
        hass.services.async_call(
            "traber", "send_command", data, blocking=True, return_response=True
        )
    )
    await asyncio.wait_for(arrived.wait(), 5)
    assert len(events) == 1 and events[0]["kind"] == "command_request"
    assert events[0]["installation_id"] == installation
    connection.user.is_admin = False
    with pytest.raises(Unauthorized):
        commands["command_result"](
            hass,
            connection,
            {"id": 4, "request_id": events[0]["request_id"], "result": {"status": "accepted"}},
        )
    connection.user.is_admin = True
    commands["command_result"](
        hass,
        connection,
        {"id": 5, "request_id": events[0]["request_id"], "result": {"status": "accepted"}},
    )
    assert (await task)["status"] == "accepted"
    connection.subscriptions[3]()
    assert (
        await hass.services.async_call(
            "traber", "send_command", data, blocking=True, return_response=True
        )
    )["status"] == "disconnected"
    # Switch Manager can invoke the same service without asking for a response.
    assert await hass.services.async_call("traber", "send_command", data, blocking=True) is None
    unload[0]()
    assert not hass.services.has_service("traber", "send_command")


def kernel(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    return hass


@pytest.mark.ha
async def test_projection_writes_only_changed_contracts_in_real_ha(tmp_path, monkeypatch):
    import copy

    from homeassistant import bootstrap

    from custom_components.traber.projection import Projection
    from custom_components.traber.sensor import ContractSensor
    from custom_components.traber.sensor import async_setup_entry as setup_sensor

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    now, expiry, writes, sensors = [0.0], [], [], []
    projection = Projection(lambda: now[0], lambda delay, callback: expiry.append(callback))
    hass.data["traber_projection"] = projection
    original_write = ContractSensor.async_write_ha_state

    @callback
    def write(sensor):
        writes.append(sensor.unique_id)
        original_write(sensor)

    monkeypatch.setattr(ContractSensor, "async_write_ha_state", write)

    @callback
    def add(additions):
        sensors.extend(additions)
        hass.async_create_task(hass.data[DATA_COMPONENT].async_add_entities(additions))

    await setup_sensor(hass, SimpleNamespace(async_on_unload=lambda callback: None), add)
    installation, epoch, owner = str(uuid4()), str(uuid4()), object()
    field = {
        "value": False,
        "status": "valid",
        "quality": "healthy",
        "since_at": None,
        "unit": None,
    }
    rows = [
        {
            "contract_id": f"fixture.value_{index}",
            "name": f"Fixture {index}",
            **field,
            "primary_field": "value",
            "fields": {"value": copy.deepcopy(field), "secondary": copy.deepcopy(field)},
        }
        for index in range(101)
    ]
    sequence = 0

    async def project():
        nonlocal sequence
        sequence += 1
        projection.accept(
            owner,
            installation,
            {
                "installation_id": installation,
                "epoch_id": epoch,
                "publication_seq": sequence,
                "contracts": rows,
            },
        )
        await hass.async_block_till_done()

    try:
        await project()
        assert len(sensors) == 101
        # HA writes during both entity setup and registration; count projections below.
        assert set(writes) == {sensor.unique_id for sensor in sensors}
        identities = [(sensor.entity_id, sensor.unique_id) for sensor in sensors]
        assert identities[0] == (
            "sensor.wuschel_fixture_value_0",
            f"{installation}:fixture.value_0",
        )
        writes.clear()
        await project()
        assert writes == []
        rows[0]["value"] = rows[0]["fields"]["value"]["value"] = True
        await project()
        assert writes == [sensors[0].unique_id]
        assert hass.states.get(sensors[0].entity_id).state == "on"
        writes.clear()
        rows[1]["fields"]["secondary"]["value"] = True
        await project()
        assert writes == [sensors[1].unique_id]
        writes.clear()
        rows[0]["value"] = rows[0]["fields"]["value"]["value"] = 1
        await project()
        assert writes == [sensors[0].unique_id]
        assert hass.states.get(sensors[0].entity_id).state == "1"
        writes.clear()
        removed = rows.pop()
        await project()
        # Removing a row also changes HA's fallback name and registry metadata.
        assert set(writes) == {sensors[-1].unique_id}
        assert hass.states.get(sensors[-1].entity_id).state == "unavailable"
        rows.append(removed)
        await project()
        assert hass.states.get(sensors[-1].entity_id).state == "off"
        writes.clear()
        epoch = str(uuid4())
        await project()
        assert len(writes) == 101
        writes.clear()
        projection.disconnect(owner)
        assert len(writes) == 101
        assert all(
            hass.states.get(sensor.entity_id).attributes["status"] == "stale" for sensor in sensors
        )
        writes.clear()
        now[0] = 120.0
        expiry.pop()()
        assert len(writes) == 101
        assert all(hass.states.get(sensor.entity_id).state == "unavailable" for sensor in sensors)
        writes.clear()
        await project()
        assert len(writes) == 101
        assert [(sensor.entity_id, sensor.unique_id) for sensor in sensors] == identities
        writes.clear()
        added = copy.deepcopy(removed)
        added["contract_id"] = "fixture.added"
        rows.append(added)
        await project()
        assert len(sensors) == 102
        assert set(writes) == {sensors[-1].unique_id}
        assert sensors[-1].entity_id == "sensor.wuschel_fixture_added"
        assert sensors[-1].unique_id == f"{installation}:fixture.added"
        writes.clear()
        old_sensors = list(sensors)
        installation = str(uuid4())
        await project()
        assert set(sensor.unique_id for sensor in old_sensors) <= set(writes)
        assert all(
            hass.states.get(sensor.entity_id).state == "unavailable" for sensor in old_sensors
        )
        assert len(sensors) == 204
    finally:
        for sensor in sensors:
            await hass.data[DATA_COMPONENT].async_remove_entity(sensor.entity_id)


@pytest.fixture(autouse=True)
def forward_platforms(monkeypatch):
    # These tests call setup directly, outside HA's ConfigEntry manager.
    forward = AsyncMock()
    monkeypatch.setattr(ConfigEntries, "async_forward_entry_setups", forward)
    return forward


@pytest.mark.ha
async def test_calendar_events_crosses_real_ha_calendar_service_schema(tmp_path, monkeypatch):
    from homeassistant import bootstrap
    from homeassistant.components.calendar import CalendarEntity, CalendarEvent
    from homeassistant.components.calendar.const import DATA_COMPONENT as CALENDAR_COMPONENT
    from homeassistant.components.calendar.services import async_setup_services
    from homeassistant.helpers.entity_component import EntityComponent

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    # Use HA's actual entity service registration and schema, with a synthetic provider.
    component = hass.data[CALENDAR_COMPONENT] = EntityComponent(
        logging.getLogger(__name__), "calendar", hass
    )
    await component.async_setup({})
    async_setup_services(hass)
    calls = []

    class FixtureCalendar(CalendarEntity):
        _attr_name = "Synthetic calendar"
        _attr_should_poll = False
        entity_id = "calendar.synthetic"

        @property
        def event(self):
            return None

        async def async_get_events(self, hass, start_date, end_date):
            calls.append((start_date, end_date))
            return [
                CalendarEvent(start=start_date, end=end_date, summary="Synthetic fixture"),
                CalendarEvent(
                    start=start_date.date() - timedelta(days=1),
                    end=start_date.date() + timedelta(days=2),
                    summary="Synthetischer Urlaub",
                ),
            ]

    await component.async_add_entities([FixtureCalendar()])
    commands, results, errors = {}, {}, []
    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    await async_setup_entry(hass, SimpleNamespace(async_on_unload=lambda callback: None))
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda identifier, result=None: results.update({identifier: result}),
        send_error=lambda *args: errors.append(args),
    )
    commands["negotiate"](hass, connection, {"id": 1, "supported_versions": [2]})
    arguments = {"start": "2026-10-24T00:00:00+02:00", "end": "2026-10-26T00:00:00+01:00"}
    commands["service_call"](
        hass,
        connection,
        {
            "id": 2,
            "protocol_version": 2,
            "request_id": str(uuid4()),
            "operation": "calendar_events",
            "target_ids": ["calendar.synthetic"],
            "arguments": arguments,
        },
    )
    await hass.async_block_till_done()
    assert not errors
    assert results[2]["outcome"] == "completed"
    assert results[2]["data"]["calendar.synthetic"]["events"][0]["summary"] == "Synthetic fixture"
    overlapping = results[2]["data"]["calendar.synthetic"]["events"][1]
    assert overlapping["start"] < arguments["start"][:10] < overlapping["end"]
    assert calls == [
        (datetime.fromisoformat(arguments["start"]), datetime.fromisoformat(arguments["end"]))
    ]
    await component.async_remove_entity("calendar.synthetic")


@pytest.mark.ha
async def test_projected_sensor_uses_real_ha_state_engine(tmp_path, monkeypatch, forward_platforms):
    from custom_components.traber.sensor import async_setup_entry as setup_sensor

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    from homeassistant import bootstrap

    # Same base setup HA performs before loading integrations (registries etc.).
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    commands, errors, entities, unload = {}, [], [], []
    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    entry = SimpleNamespace(async_on_unload=unload.append)
    await async_setup_entry(hass, entry)
    forward_platforms.assert_awaited_once_with(entry, ["sensor"])
    await setup_sensor(hass, entry, entities.extend)
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda *args: None,
        send_error=lambda *args: errors.append(args),
    )
    installation = str(uuid4())
    commands["negotiate"](
        hass, connection, {"id": 1, "supported_versions": [2], "installation_id": installation}
    )
    message = {
        "id": 2,
        "installation_id": installation,
        "epoch_id": str(uuid4()),
        "publication_seq": 1,
        "contracts": [
            {
                "contract_id": "fixture.value",
                "name": "Fixture",
                "value": "on",
                "status": "valid",
                "quality": "healthy",
                "since_at": "2026-10-24T00:00:00Z",
                "unit": None,
            }
        ],
    }
    commands["project_contracts"](hass, connection, message)
    assert not errors and len(entities) == 1
    sensor = entities[0]
    await hass.data[DATA_COMPONENT].async_add_entities(entities)
    await hass.async_block_till_done()
    assert sensor.entity_id.startswith("sensor.wuschel_fixture_value")
    state = hass.states.get(sensor.entity_id)
    assert state.state == "on" and state.attributes["contract_id"] == "fixture.value"
    message["publication_seq"] += 1
    message["contracts"][0].update(status="unknown", value="on", quality="degraded")
    commands["project_contracts"](hass, connection, message)
    assert hass.states.get(sensor.entity_id).state == "unknown"
    assert hass.states.get(sensor.entity_id).attributes["status"] == "unknown"
    connection.subscriptions[1]()
    assert hass.states.get(sensor.entity_id).state == "unknown"
    assert hass.states.get(sensor.entity_id).attributes["status"] == "stale"
    projection = hass.data["traber_projection"]
    projection.now = lambda: projection.disconnected_at + 121
    projection.notify()
    assert hass.states.get(sensor.entity_id).state == "unavailable"
    commands["project_contracts"](hass, connection, message)
    assert errors[-1][1] == "unsupported_protocol"


@pytest.mark.ha
async def test_paused_projection_expires_on_open_socket_and_same_snapshot_recovers(tmp_path):
    from homeassistant import bootstrap

    from custom_components.traber.projection import Projection
    from custom_components.traber.sensor import ContractSensor

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    now, callbacks = [0.0], []

    def schedule(delay, callback):
        callbacks.append((delay, callback))
        return SimpleNamespace(cancel=lambda: None)

    projection = Projection(now=lambda: now[0], schedule=schedule)
    installation, epoch, owner = str(uuid4()), str(uuid4()), object()
    field = {"value": True, "status": "valid", "quality": "healthy", "since_at": None, "unit": None}
    row = {
        "contract_id": "synthetic.pause",
        "name": "Synthetic pause",
        **field,
        "primary_field": "value",
        "fields": {"value": field},
    }
    message = {
        "installation_id": installation,
        "epoch_id": epoch,
        "publication_seq": 1,
        "contracts": [row],
    }
    assert projection.accept(owner, installation, message)
    sensor = ContractSensor(projection, installation, row["contract_id"])
    await hass.data[DATA_COMPONENT].async_add_entities([sensor])
    await hass.async_block_till_done()
    now[0] = 119
    projection.notify()
    assert hass.states.get(sensor.entity_id).state == "on"
    assert hass.states.get(sensor.entity_id).attributes["status"] == "valid"
    now[0] = 120
    delay, expire = callbacks[-1]
    assert delay == 120
    expire()
    state = hass.states.get(sensor.entity_id)
    assert projection.connected and not projection.fresh
    assert state.state == "unknown" and state.attributes["status"] == "stale"
    assert state.attributes["fields"]["value"]["status"] == "stale"
    assert state.attributes["fields"]["value"]["quality"] == "degraded"
    assert projection.accept(owner, installation, message)
    assert hass.states.get(sensor.entity_id).state == "on"
    assert hass.states.get(sensor.entity_id).attributes["status"] == "valid"
    assert hass.states.get(sensor.entity_id).attributes["fields"]["value"]["status"] == "valid"


@pytest.mark.ha
async def test_b23_live_size_reload_restart_reprojects_all_entities_under_ten_seconds(tmp_path):
    from time import perf_counter

    import aiohttp
    from aiohttp import web
    from homeassistant import bootstrap

    from core_contracts.adapters.ha import BridgeAdapter
    from custom_components.traber.projection import Projection
    from custom_components.traber.sensor import ContractSensor
    from dev.fake_ha import FakeHA
    from tests.test_b23_acceptance import ha_states, scale_runtime, wait_for

    runtime, _ = await scale_runtime()
    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    fake = FakeHA(ha_states(runtime), v2=True, projection=True)
    fake.projection_cache = Projection(hass.loop.time, hass.loop.call_later)
    server = web.AppRunner(fake.application())
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    tasks, sensors = [], []
    try:
        async with aiohttp.ClientSession() as session:
            adapter = BridgeAdapter(
                runtime, session, f"http://127.0.0.1:{port}/websocket", "synthetic"
            )
            tasks = [
                asyncio.create_task(adapter.run()),
                asyncio.create_task(adapter.projection_worker()),
            ]
            expected_ids = None
            for mode in ("initial", "reload", "restart"):
                started = perf_counter()
                if mode != "initial":
                    for sensor in sensors:
                        await hass.data[DATA_COMPONENT].async_remove_entity(sensor.entity_id)
                    fake.projection_cache = Projection(hass.loop.time, hass.loop.call_later)
                    if mode == "reload":
                        await fake.emit({"kind": "bridge_reloaded"})
                    else:
                        await fake.disconnect()
                await wait_for(
                    lambda: (
                        len(fake.projection_cache.rows) == 100
                        and all(
                            row["status"] == "valid" for row in fake.projection_cache.rows.values()
                        )
                    )
                )
                sensors = [
                    ContractSensor(fake.projection_cache, runtime.installation_id, key)
                    for key in fake.projection_cache.rows
                ]
                await hass.data[DATA_COMPONENT].async_add_entities(sensors)
                await hass.async_block_till_done()
                ids = sorted(sensor.entity_id for sensor in sensors)
                if expected_ids is None:
                    expected_ids = ids
                assert ids == expected_ids
                assert all(hass.states.get(identity).state in {"on", "off"} for identity in ids)
                assert perf_counter() - started < 10
                assert not runtime.overflow and not runtime.resubscribe.is_set()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.cleanup()
        await runtime.stop()


@pytest.mark.parametrize(
    "value,status,unit,expected,precision",
    [
        (True, "valid", None, "on", None),
        (False, "valid", None, "off", None),
        (None, "not_applicable", "W", "not_applicable", None),
        (3.05555555555556, "valid", "W", "3.05555555555556", 1),
    ],
)
async def test_b23_state_format_and_raw_values_in_real_ha(
    tmp_path, value, status, unit, expected, precision
):
    from homeassistant import bootstrap

    from custom_components.traber.projection import Projection
    from custom_components.traber.sensor import ContractSensor

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    projection = Projection()
    installation, owner = str(uuid4()), object()
    row = {
        "contract_id": "fixture.format",
        "name": "Synthetic format",
        "value": value,
        "status": status,
        "quality": "healthy",
        "since_at": None,
        "unit": unit,
    }
    projection.accept(
        owner,
        installation,
        {
            "installation_id": installation,
            "epoch_id": str(uuid4()),
            "publication_seq": 1,
            "contracts": [row],
        },
    )
    sensor = ContractSensor(projection, installation, row["contract_id"])
    await hass.data[DATA_COMPONENT].async_add_entities([sensor])
    await hass.async_block_till_done()
    state = hass.states.get(sensor.entity_id)
    assert state.state == expected
    assert state.attributes["raw_value"] == value
    assert sensor.suggested_display_precision == precision


@pytest.mark.ha
async def test_real_state_reported_callback_and_unsubscribe(tmp_path, monkeypatch):
    hass = kernel(tmp_path)
    commands = {}
    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    unload = []
    entry = SimpleNamespace(async_on_unload=unload.append)
    events = []
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda *args: None,
        send_event=lambda identifier, event: events.append(event),
        send_error=lambda *args: events.append({"error": args[1]}),
    )
    await async_setup_entry(hass, entry)
    hass.states.async_set("sensor.fixture", "on")
    await hass.async_block_till_done()
    commands["subscribe"](
        hass, connection, {"id": 1, "protocol_version": 1, "entity_ids": ["sensor.fixture"]}
    )
    snapshot = next(event for event in events if event.get("kind") == "snapshot")
    initial_state = hass.states.get("sensor.fixture")
    for name in ("last_changed", "last_updated", "last_reported"):
        assert (
            snapshot["states"]["sensor.fixture"][name] == getattr(initial_state, name).isoformat()
        )
    # The state engine itself emits state_reported with its real event.data.
    hass.states.async_set("sensor.fixture", "on")
    await hass.async_block_till_done()
    reports = [event for event in events if event.get("kind") == "reported"]
    assert len(reports) == 1
    assert (
        reports[0]["last_reported"] == hass.states.get("sensor.fixture").last_reported.isoformat()
    )
    assert reports[0]["time_fired"]
    for number in range(2, 102):
        commands["subscribe"](
            hass,
            connection,
            {
                "id": number,
                "protocol_version": 1,
                "entity_ids": ["sensor.fixture"],
                "report_entity_ids": [],
            },
        )
        connection.subscriptions.pop(number)()
    assert len(unload) == 1
    assert len(hass.data["traber"]) == 1
    connection.subscriptions.pop(1)()
    count = len(events)
    hass.states.async_set("sensor.fixture", "on")
    await hass.async_block_till_done()
    assert len(events) == count
    unload[0]()
    commands["subscribe"](hass, connection, {"id": 103, "protocol_version": 1, "entity_ids": []})
    assert events[-1]["error"] == "bridge_unloaded"


@pytest.mark.ha
async def test_v2_negotiation_manifest_admin_services_and_context(tmp_path, monkeypatch):
    hass = kernel(tmp_path)
    commands, results, errors, events, calls = {}, {}, [], [], []
    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    unload = []
    await async_setup_entry(hass, SimpleNamespace(async_on_unload=unload.append))
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda identifier, result=None: results.update({identifier: result}),
        send_error=lambda *args: errors.append(args),
        send_event=lambda identifier, event: events.append(event),
    )
    commands["info"](hass, connection, {"id": 1})
    manifest = json.loads(
        (Path(__file__).parents[1] / "custom_components/traber/manifest.json").read_text()
    )
    assert results[1]["bridge_version"] == manifest["version"]
    assert results[1]["protocol_version"] == 1
    msg = {
        "id": 2,
        "protocol_version": 2,
        "request_id": str(uuid4()),
        "operation": "calendar_events",
        "target_ids": ["calendar.example"],
        "arguments": {"start": "2026-10-17T00:00:00Z", "end": "2026-10-18T00:00:00Z"},
    }
    commands["service_call"](hass, connection, msg)
    await hass.async_block_till_done()
    assert errors[-1][1] == "unsupported_protocol"
    commands["negotiate"](hass, connection, {"id": 3, "supported_versions": [1, 2]})
    assert results[3]["protocol_selected"] == 2
    assert results[3]["bridge_version"] == manifest["version"]

    async def calendar(call):
        calls.append(call)
        return {"calendar.example": {"events": []}}

    hass.services.async_register(
        "calendar", "get_events", calendar, supports_response=SupportsResponse.ONLY
    )
    commands["service_call"](hass, connection, {**msg, "id": 4})
    await hass.async_block_till_done()
    assert results[4]["outcome"] == "completed"
    assert calls[0].data["entity_id"] == "calendar.example"
    assert len(calls) == 1
    commands["service_call"](hass, connection, {**msg, "id": 5})
    await hass.async_block_till_done()
    assert results[5] == results[4] and len(calls) == 1
    commands["negotiate"](hass, connection, {"id": 6, "supported_versions": [1]})
    commands["service_call"](hass, connection, {**msg, "id": 7})
    await hass.async_block_till_done()
    assert errors[-1][1] == "unsupported_protocol"
    connection.user.is_admin = False
    with pytest.raises(Unauthorized):
        commands["negotiate"](hass, connection, {"id": 8, "supported_versions": [2]})
    with pytest.raises(Unauthorized):
        commands["service_call"](hass, connection, {**msg, "id": 8})
    connection.user.is_admin = True
    commands["negotiate"](hass, connection, {"id": 9, "supported_versions": [2]})
    commands["subscribe"](
        hass, connection, {"id": 10, "protocol_version": 2, "entity_ids": ["sensor.fixture"]}
    )
    context = Context(user_id="fixture-user", parent_id="fixture-parent")
    hass.states.async_set("sensor.fixture", "on", context=context)
    await hass.async_block_till_done()
    assert (
        next(event for event in events if event.get("kind") == "changed")["context"]
        == context.as_dict()
    )
    hass.states.async_set("sensor.fixture", "on", context=context)
    await hass.async_block_till_done()
    assert (
        next(event for event in events if event.get("kind") == "reported")["context"]
        == context.as_dict()
    )
    automatic = Context(parent_id=context.id)
    hass.data["automation"] = SimpleNamespace(
        get_entity=lambda identifier: SimpleNamespace(_context=automatic)
    )
    hass.bus.async_fire(
        "automation_triggered", {"entity_id": "automation.fixture"}, context=automatic
    )
    await hass.async_block_till_done()
    proof = next(event for event in events if event.get("kind") == "automatic_context")
    assert proof["context"] == automatic.as_dict() and proof["ha_epoch_id"]
    before = len([event for event in events if event.get("kind") == "automatic_context"])
    hass.bus.async_fire(
        "automation_triggered", {"entity_id": "automation.fixture"}, context=Context()
    )
    await hass.async_block_till_done()
    assert len([event for event in events if event.get("kind") == "automatic_context"]) == before
    unload[0]()


@pytest.mark.ha
async def test_refresh_rejects_push_entities_and_accepts_actual_polling(tmp_path, monkeypatch):
    from homeassistant.helpers.entity_component import DATA_INSTANCES

    hass = kernel(tmp_path)
    commands, results, calls = {}, {}, []
    monkeypatch.setattr(
        websocket_api,
        "async_register_command",
        lambda hass, handler: commands.update({handler.__name__: handler}),
    )
    await async_setup_entry(hass, SimpleNamespace(async_on_unload=lambda callback: None))
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        subscriptions={},
        send_result=lambda identifier, result=None: results.update({identifier: result}),
        send_error=lambda *args: pytest.fail(str(args)),
    )
    commands["negotiate"](hass, connection, {"id": 1, "supported_versions": [2]})
    entity = SimpleNamespace(should_poll=False)
    hass.data[DATA_INSTANCES] = {"sensor": SimpleNamespace(get_entity=lambda identifier: entity)}

    @callback
    def update(call):
        calls.append(call.data)

    hass.services.async_register("homeassistant", "update_entity", update)
    msg = {
        "id": 2,
        "protocol_version": 2,
        "request_id": str(uuid4()),
        "operation": "source_refresh",
        "target_ids": ["sensor.example"],
        "arguments": {"profile": "entity_poll"},
    }
    commands["service_call"](hass, connection, msg)
    await hass.async_block_till_done()
    assert results[2]["outcome"] == "rejected" and calls == []
    entity.should_poll = True
    commands["service_call"](hass, connection, {**msg, "id": 3, "request_id": str(uuid4())})
    await hass.async_block_till_done()
    assert results[3]["outcome"] == "completed" and calls == [{"entity_id": "sensor.example"}]


@pytest.mark.ha
async def test_companion_refresh_uses_registered_device_only_and_metadata_prefers_entity_area(
    tmp_path, monkeypatch
):
    from homeassistant.helpers import area_registry, device_registry, entity_registry

    hass = kernel(tmp_path)
    registration = SimpleNamespace(
        config_entry_id="mobile-entry", platform="mobile_app", area_id="office", device_id="device"
    )
    monkeypatch.setattr(
        entity_registry,
        "async_get",
        lambda hass: SimpleNamespace(
            async_get=lambda identifier: (
                registration if identifier == "device_tracker.phone" else None
            )
        ),
    )
    monkeypatch.setattr(
        device_registry,
        "async_get",
        lambda hass: SimpleNamespace(
            async_get=lambda identifier: SimpleNamespace(area_id="living")
        ),
    )
    monkeypatch.setattr(
        area_registry,
        "async_get",
        lambda hass: SimpleNamespace(
            async_get_area=lambda identifier: SimpleNamespace(
                name="Büro" if identifier == "office" else "Wohnzimmer", icon="mdi:desk"
            )
        ),
    )
    monkeypatch.setattr(
        hass,
        "config_entries",
        SimpleNamespace(
            async_get_entry=lambda identifier: SimpleNamespace(
                domain="mobile_app", data={"webhook_id": "phone-registration"}
            )
        ),
        raising=False,
    )
    hass.data["mobile_app"] = {
        "notify": SimpleNamespace(
            registered_targets={"mobile_app_example_phone": "phone-registration"}
        )
    }
    hass.services.async_register("notify", "mobile_app_example_phone", lambda call: None)
    assert refresh_action(
        hass,
        "companion_location",
        {"entity_id": "device_tracker.phone", "permission_confirmed": True},
    ) == ("notify", "mobile_app_example_phone", {"message": "request_location_update"})
    with pytest.raises(Rejected):
        refresh_action(
            hass,
            "companion_location",
            {"entity_id": "device_tracker.phone", "permission_confirmed": False},
        )
    with pytest.raises(Rejected):
        refresh_action(
            hass,
            "companion_location",
            {"entity_id": "device_tracker.other", "permission_confirmed": True},
        )
    metadata = source_metadata(hass, ["device_tracker.phone"])
    assert metadata == [
        {
            "entity_id": "device_tracker.phone",
            "area_id": "office",
            "area_name": "Büro",
            "area_icon": "mdi:desk",
            "refresh_profiles": ["companion_location"],
        }
    ]
    registration.area_id = None
    assert source_metadata(hass, ["device_tracker.phone"])[0]["area_name"] == "Wohnzimmer"


@pytest.mark.ha
async def test_multifield_projection_sensor_attributes_in_real_ha_kernel(tmp_path):
    from homeassistant import bootstrap

    from custom_components.traber.projection import Projection
    from custom_components.traber.sensor import ContractSensor

    hass = kernel(tmp_path)
    loader.async_setup(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "sensor", {})
    projection = Projection()
    installation, epoch, owner = str(uuid4()), str(uuid4()), object()
    fields = {
        "state": {
            "value": "active",
            "status": "valid",
            "quality": "healthy",
            "since_at": "2026-01-01T00:00:00Z",
            "unit": None,
        },
        "detail": {
            "value": "Synthetic detail",
            "status": "valid",
            "quality": "degraded",
            "since_at": "2026-01-01T00:01:00Z",
            "unit": None,
        },
    }
    row = {
        "contract_id": "example.public.state",
        "name": "Synthetic public contract",
        **fields["state"],
        "primary_field": "state",
        "fields": fields,
    }
    message = {
        "installation_id": installation,
        "epoch_id": epoch,
        "publication_seq": 1,
        "contracts": [row],
    }
    assert projection.accept(owner, installation, message)
    sensor = ContractSensor(projection, installation, row["contract_id"])
    await hass.data[DATA_COMPONENT].async_add_entities([sensor])
    await hass.async_block_till_done()
    state = hass.states.get(sensor.entity_id)
    assert state.state == "active"
    assert state.attributes["primary_field"] == "state"
    assert state.attributes["fields"]["detail"] == fields["detail"]
    assert state.attributes["fields"]["detail"]["quality"] == "degraded"
    projection.disconnect(owner)
    assert hass.states.get(sensor.entity_id).state == "active"
    assert hass.states.get(sensor.entity_id).attributes["status"] == "stale"
    projection.now = lambda: projection.disconnected_at + 121
    projection.notify()
    assert hass.states.get(sensor.entity_id).state == "unavailable"
