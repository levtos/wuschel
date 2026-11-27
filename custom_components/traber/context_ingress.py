"""Bounded acknowledgements for context registrations on the command connection."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import voluptuous as vol

FEATURE = "context_registration_v1"
TIMEOUT_S = 30


def lifetime_seconds(value):
    if type(value) is not int or not 1 <= value <= 600:
        raise vol.Invalid("Expected whole seconds between 1 and 600")
    return value


SCHEMA = vol.Schema(
    {
        vol.Required("context_id"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("producer_id"): vol.Match(r"^[a-z][a-z0-9_.-]{1,127}$"),
        vol.Optional("lifetime_s", default=600): lifetime_seconds,
    }
)


class ContextIngress:
    def __init__(self, epoch, now):
        self.epoch = epoch
        self.now = now
        self.owner = self.installation = self.send = None
        self.pending = {}

    def attach(self, owner, installation, send):
        self.detach(self.owner)
        self.owner, self.installation, self.send = owner, installation, send

    def detach(self, owner):
        if self.owner is not owner:
            return
        for future in self.pending.values():
            if not future.done():
                # Wuschel may already have committed the request.
                future.set_result({"status": "unknown_result", "reason": "disconnected"})
        self.pending.clear()
        self.owner = self.installation = self.send = None

    async def call(self, data):
        if self.send is None:
            return {"status": "rejected", "reason": "disconnected"}
        if len(self.pending) >= 64:
            return {"status": "rejected", "reason": "busy"}
        identity, issued = str(uuid4()), self.now()
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            self.send(
                {
                    "kind": "context_register",
                    "request_id": identity,
                    "installation_id": self.installation,
                    "ha_epoch_id": self.epoch,
                    "issued_at": issued.isoformat(),
                    "valid_until": (issued + timedelta(seconds=TIMEOUT_S)).isoformat(),
                    **data,
                }
            )
            return await asyncio.wait_for(future, TIMEOUT_S)
        except TimeoutError:
            return {"status": "unknown_result", "reason": "timeout"}
        finally:
            self.pending.pop(identity, None)

    def complete(self, owner, identity, result):
        future = self.pending.get(identity)
        if (
            owner is not self.owner
            or future is None
            or future.done()
            or result.get("status") not in ("accepted", "rejected", "unknown_result")
        ):
            return False
        future.set_result(
            {key: result[key] for key in ("status", "reason", "expires_at") if key in result}
        )
        return True
