"""Admin-only transport. No quality, normalization, retention or domain state."""

import json
from pathlib import Path
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)

from .projection import Projection
from .service_channel import OPERATIONS, ServiceChannel
from .source_metadata import refresh_action, source_metadata

DOMAIN = "traber"
HA_EPOCH_ID = str(uuid4())
VERSION = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))[
    "version"
]


async def async_setup_entry(hass, entry):
    active = set()
    channels = {}
    hass.data[DOMAIN] = active
    projection = Projection()
    hass.data["traber_projection"] = projection

    @callback
    def unload():
        hass.data.pop(DOMAIN, None)
        hass.data.pop("traber_projection", None)
        projection.disconnect(projection.owner)
        for channel in channels.values():
            channel.close()
        channels.clear()
        for disconnect in tuple(active):
            disconnect()

    entry.async_on_unload(unload)

    async def execute(domain, service, data, response):
        if domain == "refresh":
            domain, service, data = refresh_action(hass, service, data)
        return await hass.services.async_call(
            domain, service, data, blocking=True, return_response=response
        )

    @callback
    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/negotiate",
            vol.Required("supported_versions"): [int],
            vol.Optional("installation_id"): str,
        }
    )
    @websocket_api.require_admin
    def negotiate(hass, connection, msg):
        if DOMAIN not in hass.data:
            connection.send_error(msg["id"], "bridge_unloaded", "Bridge is not loaded")
            return
        selected = next(
            (version for version in (2, 1) if version in msg["supported_versions"]), None
        )
        if selected is None:
            connection.send_error(msg["id"], "unsupported_protocol", "No common protocol")
            return
        old = channels.pop(id(connection), None)
        if old:
            old.close()
            projection.disconnect(connection)
        if selected == 2:
            try:
                channel = ServiceChannel(execute, installation_id=msg.get("installation_id"))
            except ValueError:
                connection.send_error(
                    msg["id"], "invalid_arguments", "Invalid installation identity"
                )
                return
            channels[id(connection)] = channel

            @callback
            def disconnected():
                channel.close()
                projection.disconnect(connection)
                if channels.get(id(connection)) is channel:
                    channels.pop(id(connection), None)

            connection.subscriptions[msg["id"]] = disconnected
        connection.send_result(
            msg["id"],
            {
                "protocol_selected": selected,
                "bridge_version": VERSION,
                "ha_epoch_id": HA_EPOCH_ID,
                "features": list(OPERATIONS)
                + ["ha_context", "area_metadata", "contract_projection"]
                if selected == 2
                else [],
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/service_call",
            vol.Required("protocol_version"): int,
            vol.Required("request_id"): str,
            vol.Required("operation"): str,
            vol.Required("target_ids"): [str],
            vol.Required("arguments"): dict,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def service_call(hass, connection, msg):
        channel = channels.get(id(connection))
        if channel is None or msg["protocol_version"] != 2:
            connection.send_error(msg["id"], "unsupported_protocol", "Negotiate protocol 2 first")
            return
        request = {key: msg[key] for key in ("request_id", "operation", "target_ids", "arguments")}
        connection.send_result(msg["id"], await channel.call(request))

    @callback
    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/info"})
    @websocket_api.require_admin
    def info(hass, connection, msg):
        if DOMAIN not in hass.data:
            connection.send_error(msg["id"], "bridge_unloaded", "Bridge is not loaded")
            return
        connection.send_result(
            msg["id"],
            {
                "bridge_version": VERSION,
                "protocol_version": 1,
                "ha_version": HA_VERSION,
                "ha_state": str(hass.state),
            },
        )

    @callback
    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/subscribe",
            vol.Required("protocol_version"): int,
            vol.Required("entity_ids"): [str],
            vol.Optional("report_entity_ids"): [str],
        }
    )
    @websocket_api.require_admin
    def subscribe(hass, connection, msg):
        if DOMAIN not in hass.data:
            connection.send_error(msg["id"], "bridge_unloaded", "Bridge is not loaded")
            return
        if msg["protocol_version"] not in (1, 2) or (
            msg["protocol_version"] == 2 and id(connection) not in channels
        ):
            connection.send_error(msg["id"], "unsupported_protocol", "Bridge protocol 1 required")
            return
        entities = tuple(dict.fromkeys(msg["entity_ids"]))
        reports = tuple(
            entity for entity in msg.get("report_entity_ids", entities) if entity in entities
        )

        @callback
        def changed(event):
            old, new = event.data.get("old_state"), event.data.get("new_state")
            connection.send_event(
                msg["id"],
                {
                    "kind": "changed",
                    "entity_id": event.data["entity_id"],
                    "old_state": old.as_dict() if old else None,
                    "new_state": new.as_dict() if new else None,
                    "time_fired": event.time_fired.isoformat(),
                    "context": event.context.as_dict(),
                },
            )

        @callback
        def reported(event):
            connection.send_event(
                msg["id"],
                {
                    "kind": "reported",
                    "entity_id": event.data["entity_id"],
                    "last_reported": event.data["last_reported"].isoformat(),
                    "old_last_reported": event.data["old_last_reported"].isoformat(),
                    "time_fired": event.time_fired.isoformat(),
                    "context_id": event.context.id,
                    **(
                        {"context": event.context.as_dict()} if msg["protocol_version"] == 2 else {}
                    ),
                },
            )

        # Both filtered listeners and the snapshot are installed in this callback without await.
        remove_change = async_track_state_change_event(hass, entities, changed)
        remove_report = (
            async_track_state_report_event(hass, reports, reported) if reports else lambda: None
        )
        remove_contexts = []
        if msg["protocol_version"] == 2:

            @callback
            def automatic_context(event):
                producer = event.data.get("entity_id", "")
                domain = producer.partition(".")[0]
                component = hass.data.get(domain)
                entity = (
                    component.get_entity(producer)
                    if component and hasattr(component, "get_entity")
                    else None
                )
                owned_context = getattr(entity, "_context", None)
                if (
                    domain not in {"automation", "script"}
                    or owned_context is None
                    or owned_context.id != event.context.id
                ):
                    return
                connection.send_event(
                    msg["id"],
                    {
                        "kind": "automatic_context",
                        "producer_id": producer,
                        "context": event.context.as_dict(),
                        "ha_epoch_id": HA_EPOCH_ID,
                    },
                )

            for event_type in ("automation_triggered", "script_started"):
                remove_contexts.append(hass.bus.async_listen(event_type, automatic_context))
        removed = False

        @callback
        def unsubscribe():
            nonlocal removed
            if not removed:
                removed = True
                remove_change()
                remove_report()
                for remove_context in remove_contexts:
                    remove_context()
                active.discard(reload_disconnect)

        @callback
        def reload_disconnect():
            unsubscribe()
            connection.send_event(msg["id"], {"kind": "bridge_reloaded"})

        connection.subscriptions[msg["id"]] = unsubscribe
        active.add(reload_disconnect)
        connection.send_result(msg["id"])
        connection.send_event(
            msg["id"],
            {
                "kind": "snapshot",
                "states": {
                    entity: state.as_dict() if (state := hass.states.get(entity)) else None
                    for entity in entities
                },
            },
        )

    websocket_api.async_register_command(hass, info)

    @callback
    @websocket_api.websocket_command(
        {vol.Required("type"): f"{DOMAIN}/source_metadata", vol.Required("entity_ids"): [str]}
    )
    @websocket_api.require_admin
    def metadata(hass, connection, msg):
        import re

        entities = msg["entity_ids"]
        if id(connection) not in channels:
            connection.send_error(msg["id"], "unsupported_protocol", "Negotiate protocol 2 first")
            return
        if not 1 <= len(entities) <= 200 or any(
            not re.fullmatch(r"[a-z_]+\.[a-z0-9_]{1,200}", entity) for entity in entities
        ):
            connection.send_error(msg["id"], "invalid_arguments", "Invalid entity selection")
            return
        connection.send_result(msg["id"], source_metadata(hass, entities))

    websocket_api.async_register_command(hass, metadata)
    websocket_api.async_register_command(hass, negotiate)
    websocket_api.async_register_command(hass, service_call)
    websocket_api.async_register_command(hass, subscribe)

    @callback
    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/project_contracts",
            vol.Required("installation_id"): str,
            vol.Required("epoch_id"): str,
            vol.Required("publication_seq"): int,
            vol.Required("contracts"): list,
        }
    )
    @websocket_api.require_admin
    def project_contracts(hass, connection, msg):
        channel = channels.get(id(connection))
        if channel is None:
            connection.send_error(msg["id"], "unsupported_protocol", "Negotiate protocol 2 first")
            return
        try:
            applied = projection.accept(connection, channel.installation_id, msg)
        except ValueError, TypeError, AttributeError:
            connection.send_error(msg["id"], "invalid_projection", "Invalid projection snapshot")
            return
        connection.send_result(msg["id"], {"applied": applied})

    websocket_api.async_register_command(hass, project_contracts)
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
