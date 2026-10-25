"""Deterministic HA bridge protocol fixture; contains no contract producer."""

from aiohttp import web


class FakeHA:
    def __init__(
        self, states=None, protocol=1, missing=False, registries=None, v2=False, projection=False
    ):
        self.states = states or {}
        self.protocol, self.missing = protocol, missing
        self.requests = []
        self.sockets = set()
        self.connections = []
        self.subscriptions = {}
        self.lifecycle_subscriptions = {}
        self.registries = registries or {}
        self.v2 = v2
        self.projection = projection
        self.projection_ack = True

    def application(self):
        app = web.Application()
        app.router.add_get("/websocket", self.socket)
        return app

    async def socket(self, request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.sockets.add(socket)
        await socket.send_json({"type": "auth_required"})
        await socket.receive_json()
        await socket.send_json({"type": "auth_ok"})
        requests = []
        self.connections.append(requests)
        last_id = 0
        try:
            async for message in socket:
                if message.type != web.WSMsgType.TEXT:
                    break
                data = message.json()
                self.requests.append(data)
                requests.append(data)
                if data["id"] <= last_id:
                    await socket.send_json(
                        {
                            "id": data["id"],
                            "type": "result",
                            "success": False,
                            "error": {"code": "id_reuse"},
                        }
                    )
                    continue
                last_id = data["id"]
                kind = data["type"]
                result = {}
                if kind == "traber/negotiate":
                    if not self.v2:
                        await socket.send_json(
                            {
                                "id": data["id"],
                                "type": "result",
                                "success": False,
                                "error": {"code": "unknown_command"},
                            }
                        )
                        continue
                    result = {
                        "protocol_selected": 2,
                        "bridge_version": "test",
                        "features": ["calendar_events"]
                        + (["contract_projection"] if self.projection else []),
                    }
                if kind == "traber/project_contracts":
                    if not self.projection_ack:
                        continue
                    result = {"applied": True}
                if kind == "traber/service_call":
                    result = {
                        "request_id": data["request_id"],
                        "outcome": "completed",
                        "data": {"calendar.example": {"events": []}},
                    }
                if kind == "get_states":
                    result = [
                        {"entity_id": key, **value} for key, value in self.states.items() if value
                    ]
                if kind.startswith("config/") and kind.endswith("_registry/list"):
                    result = self.registries.get(kind, [])
                    if isinstance(result, str):
                        await socket.send_json(
                            {
                                "id": data["id"],
                                "type": "result",
                                "success": False,
                                "error": {"code": result},
                            }
                        )
                        continue
                if kind == "traber/source_metadata":
                    result = [{"entity_id": entity} for entity in data["entity_ids"]]
                if kind == "subscribe_events":
                    self.lifecycle_subscriptions.setdefault(socket, {})[data["event_type"]] = data[
                        "id"
                    ]
                if kind == "traber/info":
                    if self.missing:
                        await socket.send_json(
                            {
                                "id": data["id"],
                                "type": "result",
                                "success": False,
                                "error": {"code": "unknown_command"},
                            }
                        )
                        continue
                    result = {
                        "protocol_version": self.protocol,
                        "bridge_version": "test",
                        "ha_version": "test",
                        "ha_state": "running",
                    }
                await socket.send_json(
                    {"id": data["id"], "type": "result", "success": True, "result": result}
                )
                if kind == "traber/subscribe":
                    self.subscriptions[socket] = data["id"]
                    await socket.send_json(
                        {
                            "id": data["id"],
                            "type": "event",
                            "event": {
                                "kind": "snapshot",
                                "states": {key: self.states.get(key) for key in data["entity_ids"]},
                            },
                        }
                    )
        finally:
            self.sockets.discard(socket)
            self.subscriptions.pop(socket, None)
            self.lifecycle_subscriptions.pop(socket, None)
        return socket

    async def emit(self, event):
        for socket, subscription_id in list(self.subscriptions.items()):
            await socket.send_json({"id": subscription_id, "type": "event", "event": event})

    async def lifecycle(self, event_type):
        for socket, subscriptions in list(self.lifecycle_subscriptions.items()):
            await socket.send_json(
                {
                    "id": subscriptions[event_type],
                    "type": "event",
                    "event": {"event_type": event_type},
                }
            )

    async def disconnect(self):
        for socket in list(self.sockets):
            await socket.close()
