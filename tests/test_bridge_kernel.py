"""Actual HA state engine + event helpers + our decorated Bridge callback.

Runs in the dedicated HA CI job. This is not a Supervisor installation test.
"""

import json
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


def kernel(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    return hass


@pytest.fixture(autouse=True)
def forward_platforms(monkeypatch):
    # These tests call setup directly, outside HA's ConfigEntry manager.
    forward = AsyncMock()
    monkeypatch.setattr(ConfigEntries, "async_forward_entry_setups", forward)
    return forward


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
    assert hass.states.get(sensor.entity_id).state == "unavailable"
    commands["project_contracts"](hass, connection, message)
    assert errors[-1][1] == "unsupported_protocol"


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
