"""Bounded live command handoff; no offline queue, policy or credentials."""

import asyncio
import json
from collections import OrderedDict
from datetime import timedelta
from uuid import UUID, uuid4


class CommandIngress:
    def __init__(self, epoch, now):
        self.epoch, self.now = epoch, now
        self.owner = self.installation = self.send = None
        self.pending = {}
        self.completed = OrderedDict()

    def attach(self, owner, installation, send):
        UUID(installation)
        if self.owner is not None and self.owner is not owner:
            raise ValueError("command_owner_busy")
        self.detach(owner)
        self.owner, self.installation, self.send = owner, installation, send

    def detach(self, owner):
        if self.owner is not owner:
            return
        for _, future in self.pending.values():
            if not future.done():
                future.set_result({"status": "disconnected"})
        self.pending.clear()
        self.completed.clear()
        self.owner = self.installation = self.send = None

    async def call(self, data):
        identity = str(data.get("request_id") or uuid4())
        try:
            UUID(identity)
            if set(data) - {"request_id", "contract_id", "command", "arguments"}:
                raise ValueError
            if not all(
                isinstance(data.get(name), str) and 0 < len(data[name]) <= 128
                for name in ("contract_id", "command")
            ):
                raise ValueError
            if not isinstance(data.get("arguments", {}), dict):
                raise ValueError
            signature = json.dumps(
                {k: v for k, v in data.items() if k != "request_id"},
                sort_keys=True,
                allow_nan=False,
            )
            if len(signature.encode()) > 65536:
                raise ValueError
        except ValueError, TypeError:
            return {"request_id": identity, "status": "invalid_arguments"}
        if self.owner is None:
            return {"request_id": identity, "status": "disconnected"}
        for key, (_, _, expires) in tuple(self.completed.items()):
            if expires <= self.now():
                del self.completed[key]
        prior = self.completed.get(identity)
        if prior:
            return {
                "request_id": identity,
                **(prior[1] if prior[0] == signature else {"status": "request_id_conflict"}),
            }
        old = self.pending.get(identity)
        if old:
            if old[0] != signature:
                return {"request_id": identity, "status": "request_id_conflict"}
            future = old[1]
        else:
            if len(self.pending) >= 64:
                return {"request_id": identity, "status": "busy"}
            now = self.now()
            future = asyncio.get_running_loop().create_future()
            self.pending[identity] = (signature, future)
            self.send(
                {
                    "kind": "command_request",
                    "request_id": identity,
                    "installation_id": self.installation,
                    "ha_epoch_id": self.epoch,
                    "issued_at": now.isoformat(),
                    "valid_until": (now + timedelta(seconds=30)).isoformat(),
                    "contract_id": data["contract_id"],
                    "command": data["command"],
                    "arguments": data.get("arguments", {}),
                }
            )
        try:
            result = await asyncio.wait_for(asyncio.shield(future), 30)
            self.completed[identity] = (signature, result, self.now() + timedelta(seconds=30))
            while len(self.completed) > 64:
                self.completed.popitem(last=False)
            return {"request_id": identity, **result}
        except TimeoutError:
            return {"request_id": identity, "status": "unknown_result"}
        finally:
            # A timeout is never permission to generate a new command or replay on reconnect.
            self.pending.pop(identity, None)

    def complete(self, owner, identity, result):
        if owner is not self.owner or identity not in self.pending:
            return False
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            return False
        future = self.pending[identity][1]
        if not future.done():
            future.set_result(
                {key: result[key] for key in ("status", "reason", "command_id") if key in result}
            )
        return True
