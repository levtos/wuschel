"""Bridge protocol client and generic observation normalization."""

import asyncio
import logging
from datetime import datetime
from math import isfinite
from typing import Any
from uuid import uuid4

import aiohttp

from ..evidence import Observation
from ..ha_context import ContextIndex, HAContext
from ..model import utc
from ..registry import Binding, RegistryConfig
from ..runtime import Runtime
from ..value_origin import normalize as normalize_origin

LOGGER = logging.getLogger(__name__)


class BridgeRequestError(ConnectionError):
    def __init__(self, request_type: str, ha_error_code: str) -> None:
        super().__init__("bridge_request_failed")
        self.request_type = request_type
        self.ha_error_code = ha_error_code


class WebSocketRequests:
    """Own IDs and send order for exactly one authenticated WebSocket."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self.socket = socket
        self.number = 0
        self.lock = asyncio.Lock()
        self.pending: dict[int, asyncio.Future[Any]] = {}

    async def send(self, request: dict[str, Any], future: asyncio.Future[Any] | None = None) -> int:
        async with self.lock:
            self.number += 1
            if future is not None:
                self.pending[self.number] = future
            try:
                await self.socket.send_json({**request, "id": self.number})
            except BaseException:
                self.pending.pop(self.number, None)
                raise
            return self.number


def request_error(request_type: str, response: dict[str, Any]) -> BridgeRequestError:
    # HA error messages and arbitrary remote codes may contain sensitive data.
    code = response.get("error", {}).get("code")
    safe_code = (
        code
        if code in {"id_reuse", "unknown_command", "unauthorized", "invalid_format"}
        else "unknown_error"
    )
    error = BridgeRequestError(request_type, safe_code)
    LOGGER.warning(
        "bridge_request_failed",
        extra={"request_type": error.request_type, "ha_error_code": error.ha_error_code},
    )
    return error


def timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    return utc(datetime.fromisoformat(str(value)))


def transform(value: Any, binding: Binding, config: RegistryConfig) -> Any:
    def finite(number: float) -> float:
        if not isfinite(number):
            raise ValueError("non-finite number")
        return number

    transformation = binding.transform
    if transformation is None:
        return value
    if transformation.kind == "type_cast":
        if transformation.target == "number":
            if type(value) is bool:
                raise ValueError("invalid number")
            return finite(float(value))
        if transformation.target == "bool":
            if type(value) is not bool and (
                not isinstance(value, str) or value not in ("true", "false", "on", "off")
            ):
                raise ValueError("invalid boolean")
            return value in (True, "true", "on")
        return str(value)
    if transformation.kind == "unit_scale":
        assert transformation.factor is not None and transformation.offset is not None
        return finite(float(value) * transformation.factor + transformation.offset)
    catalog = next(c for c in config.catalogs if c.catalog_id == transformation.catalog_id)
    if catalog.kind != "map":
        raise ValueError("inline_catalog_required")
    return catalog.entries[str(value)]


class BridgeAdapter:
    def __init__(
        self, runtime: Runtime, session: aiohttp.ClientSession, url: str, token: str
    ) -> None:
        self.runtime, self.session, self.url, self.token = runtime, session, url, token
        self.states: dict[str, dict[str, Any] | None] = {}
        self.sequence = 0
        self.bridge_version: str | None = None
        self.protocol_selected = 1
        self.socket: aiohttp.ClientWebSocketResponse | None = None
        self.connection: WebSocketRequests | None = None
        self.projection_connected = asyncio.Event()
        self.projection_disconnected = asyncio.Event()
        self.projection_supported = False
        self.contexts = ContextIndex()
        runtime.context_index = self.contexts
        self.subscription_count = 0
        self.retry_delay = 1.0
        self.discovery_cache: list[dict[str, Any]] = []
        self.discovery_at = float("-inf")
        self.discovery_preview_cache: list[dict[str, Any]] = []
        self.discovery_preview_at = float("-inf")
        self.discovery_lock = asyncio.Lock()
        runtime.entity_discovery = self.discover

    async def discover(self, *, preview: bool = False) -> list[dict[str, Any]]:
        """Read-only discovery; bounded state previews require an explicit admin request."""
        async with self.discovery_lock:
            cached_at = self.discovery_preview_at if preview else self.discovery_at
            if self.runtime.clock.monotonic() - cached_at < 15:
                return self.discovery_preview_cache if preview else self.discovery_cache
            areas, devices, entries = {}, {}, {}
            area_metadata: list[dict[str, Any]] | None = None
            enriched: dict[str, dict[str, Any]] = {}
            async with asyncio.timeout(10):
                async with self.session.ws_connect(
                    self.url, heartbeat=20, max_msg_size=4_000_000
                ) as socket:
                    if (await socket.receive_json()).get("type") != "auth_required":
                        raise ConnectionError("ha_auth_protocol")
                    await socket.send_json({"type": "auth", "access_token": self.token})
                    if (await socket.receive_json()).get("type") != "auth_ok":
                        raise ConnectionError("ha_auth_failed")
                    connection = WebSocketRequests(socket)
                    states = await self._result(connection, {"type": "get_states"})
                    if not isinstance(states, list) or len(states) > 10000:
                        raise ValueError("discovery_resource_limit")
                    if preview:
                        registries: list[list[dict[str, Any]]] = []
                        for kind in ("area", "device", "entity"):
                            response = await self._response(
                                connection,
                                {"type": f"config/{kind}_registry/list"},
                                allowed_errors={"unknown_command", "unauthorized"},
                            )
                            if not response.get("success"):
                                if response.get("error", {}).get("code") in {
                                    "unknown_command",
                                    "unauthorized",
                                }:
                                    registries.append([])
                                    continue
                                raise ConnectionError("ha_discovery_registry_failed")
                            rows = response.get("result")
                            if not isinstance(rows, list) or len(rows) > 10000:
                                raise ValueError("discovery_resource_limit")
                            registries.append([row for row in rows if isinstance(row, dict)])
                            if kind == "area":
                                area_metadata = [
                                    {
                                        "area_id": row["area_id"][:256],
                                        "area_name": row["name"][:128],
                                        "area_icon": row.get("icon"),
                                    }
                                    for row in registries[-1]
                                    if isinstance(row.get("area_id"), str)
                                    and isinstance(row.get("name"), str)
                                    and row["name"].strip()
                                ]
                        areas = {
                            row.get("area_id"): row.get("name")
                            for row in registries[0]
                            if isinstance(row.get("area_id"), str)
                        }
                        devices = {
                            row.get("id"): row.get("area_id")
                            for row in registries[1]
                            if isinstance(row.get("id"), str)
                        }
                        entries = {
                            row.get("entity_id"): row
                            for row in registries[2]
                            if isinstance(row.get("entity_id"), str)
                        }
                        negotiated = await self._response(
                            connection,
                            {"type": "traber/negotiate", "supported_versions": [2, 1]},
                            allowed_errors={"unknown_command"},
                        )
                        if (
                            negotiated.get("success")
                            and negotiated.get("result", {}).get("protocol_selected") == 2
                        ):
                            selected_ids = [
                                row["entity_id"]
                                for row in states
                                if isinstance(row, dict) and isinstance(row.get("entity_id"), str)
                            ]
                            for offset in range(0, len(selected_ids), 200):
                                metadata_rows = await self._result(
                                    connection,
                                    {
                                        "type": "traber/source_metadata",
                                        "entity_ids": selected_ids[offset : offset + 200],
                                    },
                                )
                                if not isinstance(metadata_rows, list) or len(metadata_rows) > 200:
                                    raise ValueError("discovery_resource_limit")
                                enriched.update(
                                    {
                                        row["entity_id"]: row
                                        for row in metadata_rows
                                        if isinstance(row, dict)
                                        and row.get("entity_id")
                                        in selected_ids[offset : offset + 200]
                                    }
                                )
                        elif (
                            not negotiated.get("success")
                            and negotiated.get("error", {}).get("code") != "unknown_command"
                        ):
                            raise ConnectionError("ha_discovery_protocol")
            result: list[dict[str, Any]] = []
            for state in states:
                if not isinstance(state, dict):
                    continue
                entity = state.get("entity_id")
                if not isinstance(entity, str) or len(entity) > 256:
                    continue
                attributes = state.get("attributes", {})
                if not isinstance(attributes, dict):
                    attributes = {}

                def metadata(name: str) -> str | None:
                    value = attributes.get(name)
                    return value[:256] if isinstance(value, str) else None

                result.append(
                    {
                        "entity_id": entity,
                        "display_name": metadata("friendly_name") or entity,
                        "unit": metadata("unit_of_measurement"),
                        "device_class": metadata("device_class"),
                        "state_class": metadata("state_class"),
                        "attributes": sorted(
                            key
                            for key in attributes
                            if isinstance(key, str)
                            and len(key) <= 128
                            and not any(
                                part in key.casefold()
                                for part in (
                                    "token",
                                    "password",
                                    "secret",
                                    "credential",
                                    "key",
                                    "authorization",
                                )
                            )
                        )[:128],
                    }
                )
                if preview:
                    entry = entries.get(entity, {})
                    area_id = entry.get("area_id") or devices.get(entry.get("device_id"))
                    area_name = areas.get(area_id) if isinstance(area_id, str) else None
                    result[-1].update(
                        current_value=state["state"][:256]
                        if isinstance(state.get("state"), str)
                        else None,
                        area_id=area_id[:256] if isinstance(area_id, str) else None,
                        area_name=area_name[:256] if isinstance(area_name, str) else None,
                    )
                    detail = enriched.get(entity, {})
                    for field in ("area_id", "area_name", "area_icon"):
                        if isinstance(detail.get(field), str):
                            result[-1][field] = detail[field][:256]
                    result[-1]["refresh_profiles"] = [
                        profile
                        for profile in detail.get("refresh_profiles", [])
                        if profile in {"entity_poll", "companion_location"}
                    ]
            result = sorted(result, key=lambda item: item["entity_id"])
            if preview:
                self.runtime.discovered_areas = area_metadata
                self.discovery_preview_cache = result
                self.discovery_preview_at = self.runtime.clock.monotonic()
            else:
                self.discovery_cache = result
                self.discovery_at = self.runtime.clock.monotonic()
            return result

    def observation(
        self,
        binding: Binding,
        state: dict[str, Any] | None,
        kind: str,
        event: dict[str, Any],
        config: RegistryConfig,
        revision: int | None = None,
    ) -> Observation:
        self.sequence += 1
        attributes = (state or {}).get("attributes", {})
        value = (state or {}).get("state")
        availability = "absent" if state is None else "available"
        if value in ("unknown", "unavailable"):
            availability = value
        if binding.adapter.kind == "ha_attribute":
            value = attributes.get(binding.adapter.ha_attribute)
        normalized = value
        transform_error = None
        origin_fields: dict[str, Any] = {}
        if availability == "available":
            try:
                metadata = (
                    attributes[binding.adapter.value_origin_path]
                    if binding.adapter.value_origin_path
                    else None
                )
                normalized, origin_fields = normalize_origin(value, metadata, binding, config)
            except KeyError:
                transform_error = "unmapped_value"
                availability = "unknown"
            except ValueError, TypeError:
                transform_error = "invalid_value"
                availability = "unknown"
        source = next(s for s in config.sources if s.source_id == binding.source_id)
        raw_times = {
            "device_time": attributes.get(binding.adapter.ha_time_attribute)
            if binding.adapter.ha_time_attribute
            else None,
            "ha_last_changed": (state or {}).get("last_changed"),
            "ha_last_updated": (state or {}).get("last_updated"),
            "ha_last_reported": event.get("last_reported", (state or {}).get("last_reported")),
            "ha_time_fired": event.get("time_fired"),
        }
        times: dict[str, datetime | None] = {}
        invalid_timestamp = False
        for key, raw_time in raw_times.items():
            try:
                times[key] = timestamp(raw_time)
            except ValueError, TypeError, OverflowError:
                times[key] = None
                invalid_timestamp = True
        context = None
        attribution = None
        if self.protocol_selected == 2:
            raw_context = (
                (state or {}).get("context") if kind == "snapshot" else event.get("context")
            )
            context = HAContext.model_validate(raw_context or {"id": event.get("context_id")})
            attribution = self.contexts.attribution(context, self.runtime.clock.now_utc())
        return Observation.model_validate(
            {
                "source_id": binding.source_id,
                "binding_id": binding.binding_id,
                "adapter": binding.adapter.kind,
                "source_origin": source.source_origin,
                "value_raw": value,
                "value_normalized": normalized,
                "normalized": True,
                **origin_fields,
                **times,
                "invalid_timestamp": invalid_timestamp,
                "transform_error": transform_error,
                "ha_context_id": event.get("context_id", (event.get("context") or {}).get("id")),
                **(
                    {"ha_context": context, "attribution": attribution, "ha_context_id": context.id}
                    if context
                    else {}
                ),
                "received_at": self.runtime.clock.now_utc(),
                "observation_kind": kind,
                "ha_restored": bool(attributes.get("restored", False)),
                "assumed_state": bool(attributes.get("assumed_state", False)),
                "epoch_id": self.runtime.state.epoch_id,
                "ingest_seq": self.sequence,
                "reference_version": 2 if config.fingerprint_version >= 2 else 1,
                "registry_revision": self.runtime.state.active_revision
                if revision is None
                else revision,
                "availability": availability,
            }
        )

    async def event(
        self, event: dict[str, Any], config: RegistryConfig, revision: int | None = None
    ) -> None:
        kind = event["kind"]
        if kind == "automatic_context":
            if self.protocol_selected != 2 or event.get("ha_epoch_id") != self.contexts.epoch:
                return
            if str(event.get("producer_id", "")).partition(".")[0] not in {"automation", "script"}:
                return
            self.contexts.register(
                HAContext.model_validate(event.get("context", {})),
                self.runtime.clock.now_utc(),
                basis="ha_run",
            )
            return
        if kind == "bridge_reloaded":
            raise ConnectionError("bridge_reloaded")
        if kind == "snapshot":
            self.states = event["states"]
            observations = []
            for binding in config.bindings:
                entity = binding.adapter.ha_entity_id
                if entity and binding.adapter.kind in {"ha_state", "ha_attribute"}:
                    observations.append(
                        self.observation(
                            binding, self.states.get(entity), "snapshot", {}, config, revision
                        )
                    )
            self.runtime.ingest_snapshot(observations, revision)
            return
        entity = event["entity_id"]
        if kind == "changed":
            self.states[entity] = event["new_state"]
        elif kind == "reported" and self.states.get(entity) is not None:
            self.states[entity] = {
                **(self.states[entity] or {}),
                "last_reported": event["last_reported"],
            }
        for binding in config.bindings:
            if binding.adapter.ha_entity_id == entity and binding.adapter.kind in {
                "ha_state",
                "ha_attribute",
            }:
                self.runtime.ingest(
                    self.observation(
                        binding,
                        self.states.get(entity),
                        "report" if kind == "reported" else "live_change",
                        event,
                        config,
                        revision,
                    )
                )

    async def _response(
        self,
        connection: WebSocketRequests,
        request: dict[str, Any],
        *,
        allowed_errors: set[str] | None = None,
    ) -> dict[str, Any]:
        number = await connection.send(request)
        response = await connection.socket.receive_json()
        if not isinstance(response, dict):
            raise request_error(request["type"], {})
        if response.get("id") != number:
            raise request_error(request["type"], {})
        if not response.get("success") and response.get("error", {}).get("code") not in (
            allowed_errors or set()
        ):
            raise request_error(request["type"], response)
        return response

    async def _result(self, connection: WebSocketRequests, request: dict[str, Any]) -> Any:
        response = await self._response(connection, request)
        return response.get("result")

    async def connect(self, config: RegistryConfig) -> None:
        try:
            await self._connect(config)
        finally:
            self.projection_connected.clear()
            self.projection_disconnected.set()
            self.socket = None
            connection = self.connection
            self.connection = None
            if connection is not None:
                for future in connection.pending.values():
                    if not future.done():
                        future.set_result(
                            {"outcome": "indeterminate", "error_code": "ha_unavailable"}
                        )
                connection.pending.clear()

    async def service_call(
        self,
        operation: str,
        target_ids: list[str],
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """One attempt only. Callers persist intent before reaching this boundary."""
        identifier = request_id or str(uuid4())
        connection = self.connection
        if self.protocol_selected != 2 or connection is None or connection.socket.closed:
            return {
                "request_id": identifier,
                "outcome": "rejected",
                "error_code": "unsupported_protocol",
            }
        future = asyncio.get_running_loop().create_future()
        number: int | None = None
        try:
            async with asyncio.timeout(12):
                number = await connection.send(
                    {
                        "type": "traber/service_call",
                        "protocol_version": 2,
                        "request_id": identifier,
                        "operation": operation,
                        "target_ids": target_ids,
                        "arguments": arguments,
                    },
                    future,
                )
                response = await future
                if not isinstance(response, dict):
                    raise ValueError("bridge_invalid_response")
                return {"request_id": identifier, **response}
        except TimeoutError, aiohttp.ClientError, ConnectionError:
            return {"request_id": identifier, "outcome": "indeterminate", "error_code": "timeout"}
        finally:
            if number is not None:
                connection.pending.pop(number, None)

    async def _connect(self, config: RegistryConfig) -> None:
        revision = self.runtime.state.active_revision
        self.runtime.resubscribe.clear()
        self.runtime.revision_changed.clear()
        async with self.session.ws_connect(
            self.url, heartbeat=20, max_msg_size=4_000_000
        ) as socket:
            if (await socket.receive_json()).get("type") != "auth_required":
                raise ConnectionError("ha_auth_protocol")
            await socket.send_json({"type": "auth", "access_token": self.token})
            if (await socket.receive_json()).get("type") != "auth_ok":
                raise ConnectionError("ha_auth_failed")
            connection = WebSocketRequests(socket)
            info = await self._result(connection, {"type": "traber/info"})
            if info.get("protocol_version") != 1:
                raise ConnectionError("bridge_incompatible")
            self.bridge_version = info["bridge_version"]
            self.runtime.bridge_version = self.bridge_version
            negotiated = await self._response(
                connection,
                {
                    "type": "traber/negotiate",
                    "supported_versions": [2, 1],
                    **(
                        {"installation_id": self.runtime.installation_id}
                        if self.runtime.installation_id
                        else {}
                    ),
                },
                allowed_errors={"unknown_command"},
            )
            self.protocol_selected = 1
            features: list[str] = []
            if negotiated.get("success"):
                selected = negotiated.get("result", {})
                if selected.get("protocol_selected") not in (1, 2):
                    raise ConnectionError("bridge_incompatible")
                self.protocol_selected = selected["protocol_selected"]
                self.contexts.set_epoch(selected.get("ha_epoch_id"))
                features = list(selected.get("features", []))
            elif negotiated.get("error", {}).get("code") != "unknown_command":
                raise ConnectionError("bridge_negotiation_failed")
            if self.protocol_selected == 1:
                self.contexts.set_epoch(None)
            self.runtime.bridge_protocol_selected = self.protocol_selected
            self.runtime.bridge_features = features
            self.projection_supported = "contract_projection" in features
            await self._result(connection, {"type": "get_config"})
            for event_type in ("homeassistant_started", "homeassistant_stop"):
                await self._result(
                    connection, {"type": "subscribe_events", "event_type": event_type}
                )
            entities = sorted(
                {b.adapter.ha_entity_id for b in config.bindings if b.adapter.ha_entity_id}
            )
            self.subscription_count = len(entities)
            self.runtime.bridge_subscriptions = self.subscription_count
            live_sources = {s.freshness.liveness_source for s in config.sources}
            report_sources = {
                s.source_id
                for s in config.sources
                if s.freshness.mode != "event_stateful" or s.source_id in live_sources
            }
            subscribed = await self._response(
                connection,
                {
                    "type": "traber/subscribe",
                    "protocol_version": self.protocol_selected,
                    "entity_ids": entities,
                    "report_entity_ids": sorted(
                        {
                            b.adapter.ha_entity_id
                            for b in config.bindings
                            if b.adapter.ha_entity_id and b.source_id in report_sources
                        }
                    ),
                },
            )
            subscription_id = subscribed["id"]
            await self.runtime.submit(
                "bridge_state",
                (
                    "connected",
                    "connected"
                    if str(info.get("ha_state", "")).lower() == "running"
                    else "starting",
                ),
            )
            self.retry_delay = 1.0
            LOGGER.info("bridge_connected")
            self.socket = socket
            self.connection = connection
            self.projection_disconnected.clear()
            self.projection_connected.set()
            while True:
                receive = asyncio.create_task(socket.receive_json())
                change = asyncio.create_task(self.runtime.revision_changed.wait())
                overflow = asyncio.create_task(self.runtime.resubscribe.wait())
                pending = {receive, change, overflow}
                try:
                    done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    if overflow in done:
                        return
                    if change in done:
                        from ..reconfiguration import assessment_only

                        current = self.runtime.config
                        if not assessment_only(config, current):
                            return
                        assert current is not None
                        config, revision = current, self.runtime.state.active_revision
                        self.runtime.revision_changed.clear()
                        if receive not in done:
                            continue
                    message = receive.result()
                    if message.get("type") == "result" and message.get("id") in connection.pending:
                        if not message.get("success"):
                            request_error("traber/service_call", message)
                        future = connection.pending[message["id"]]
                        if not future.done():
                            future.set_result(
                                message.get("result")
                                if message.get("success")
                                else {"outcome": "rejected", "error_code": "ha_unavailable"}
                            )
                        continue
                    if message.get("type") == "event":
                        if message["id"] == subscription_id:
                            await self.event(message["event"], config, revision)
                        else:
                            event_type = message.get("event", {}).get("event_type")
                            await self.runtime.submit(
                                "bridge_state",
                                (
                                    "connected",
                                    "connected"
                                    if event_type == "homeassistant_started"
                                    else "stopping",
                                ),
                            )
                finally:
                    for task in pending:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

    async def projection_worker(self) -> None:
        from ..ha_projection import snapshot

        queue = self.runtime.subscribe(max_queue=1)
        try:
            while True:
                await self.projection_connected.wait()
                connection = self.connection
                previous: dict[str, Any] | None = None
                while connection is not None and connection is self.connection:
                    payload = snapshot(self.runtime)
                    if self.projection_supported and payload != previous:
                        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                        identifier = None
                        try:
                            identifier = await connection.send(payload, future)
                            async with asyncio.timeout(20):
                                result = await future
                            if not isinstance(result, dict) or "applied" not in result:
                                raise ConnectionError("projection_not_acknowledged")
                            previous = payload
                        except TimeoutError, ConnectionError, aiohttp.ClientError:
                            self.runtime.resubscribe.set()
                            await self.projection_disconnected.wait()
                            break
                        finally:
                            if identifier is not None:
                                connection.pending.pop(identifier, None)
                    elif (
                        payload["contracts"] and not self.projection_supported and previous is None
                    ):
                        LOGGER.warning(
                            "projection_unavailable", extra={"error_type": "UnsupportedFeature"}
                        )
                        previous = payload
                    changed = asyncio.create_task(queue.get())
                    disconnected = asyncio.create_task(self.projection_disconnected.wait())
                    try:
                        done, _ = await asyncio.wait(
                            {changed, disconnected}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if disconnected in done:
                            break
                    finally:
                        for task in (changed, disconnected):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(changed, disconnected, return_exceptions=True)
        finally:
            self.runtime.subscribers.discard(queue)

    async def run(self) -> None:
        while True:
            self.runtime.resubscribe.clear()
            self.runtime.revision_changed.clear()
            config = self.runtime.config
            if config is None or not self.runtime.requires_ha_snapshot:
                self.runtime.bridge_subscriptions = 0
                if self.runtime.bridge != "not_needed" or self.runtime.ha != "not_needed":
                    await self.runtime.submit("bridge_state", ("not_needed", "not_needed"))
                await self.runtime.clock.sleep(1)
                continue
            try:
                await self.connect(config)
            except Exception as error:
                self.runtime.bridge_subscriptions = 0
                LOGGER.warning(
                    "bridge_disconnected",
                    extra={
                        "error_type": type(error).__name__,
                        "request_type": getattr(error, "request_type", None),
                        "ha_error_code": getattr(error, "ha_error_code", None),
                    },
                )
                await self.runtime.submit("bridge_state", ("unavailable", "disconnected"))
                await self.runtime.clock.sleep(self.retry_delay)
                self.retry_delay = min(self.retry_delay * 2, 30)

    async def request_refresh(self, source_id: str) -> None:
        config = self.runtime.config
        if config is None:
            raise ValueError("no registry")
        binding = next(b for b in config.bindings if b.source_id == source_id)
        entity = binding.adapter.ha_entity_id
        if entity is None:
            raise ValueError("refresh_not_supported")
        job = await self.runtime.submit(
            "legacy_refresh_prepare",
            {"revision": self.runtime.state.active_revision, "source_id": source_id},
        )
        if job is None:
            return
        result = await self.service_call(
            job["operation"], job["target_ids"], job["arguments"], request_id=job["request_id"]
        )
        await self.runtime.submit("service_complete", {"job_id": job["job_id"], "response": result})
        if result.get("outcome") != "completed":
            raise ValueError("refresh_not_supported")

    async def refresh_worker(self) -> None:
        while True:
            revision, source_id = await self.runtime.refresh_requests.get()
            try:
                if (
                    revision == self.runtime.state.active_revision
                    and self.runtime.persistence
                    and not self.runtime.stopping
                ):
                    await self.request_refresh(source_id)
            except Exception as error:
                LOGGER.warning("refresh_failed", extra={"error_type": type(error).__name__})
            finally:
                self.runtime.refresh_requests.task_done()

    async def service_worker(self) -> None:
        while True:
            self.runtime.service_jobs_changed.clear()
            job = await self.runtime.submit("service_claim")
            if job is None:
                await self.runtime.service_jobs_changed.wait()
                continue
            response = await self.service_call(
                job["operation"], job["target_ids"], job["arguments"], request_id=job["request_id"]
            )
            await self.runtime.submit(
                "service_complete", {"job_id": job["job_id"], "response": response}
            )
