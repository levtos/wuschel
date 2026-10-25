"""Bounded protocol-2 transport. No contract logic or arbitrary HA actions."""

import asyncio
import json
import re
from collections import OrderedDict
from datetime import datetime
from uuid import UUID

OPERATIONS = (
    "calendar_events",
    "problem_notice_put",
    "problem_notice_remove",
    "source_refresh",
)


class Rejected(ValueError):
    """A fixed public error code; never an exception from a provider."""


def action(request):
    """Validate the entire request before resolving any HA service or target."""
    if set(request) - {"request_id", "operation", "target_ids", "arguments"}:
        raise Rejected("invalid_arguments")
    UUID(request["request_id"])
    operation, targets, args = request["operation"], request["target_ids"], request["arguments"]
    if operation not in OPERATIONS:
        raise Rejected("unsupported_operation")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(args, dict):
        raise Rejected("invalid_arguments")
    target = targets[0]
    if not isinstance(target, str) or len(target) > 256:
        raise Rejected("invalid_arguments")
    if operation == "calendar_events":
        if not re.fullmatch(r"calendar\.[a-z0-9_]+", target) or set(args) != {"start", "end"}:
            raise Rejected("not_allowed")
        start, end = (datetime.fromisoformat(args[key]) for key in ("start", "end"))
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or not 0 < (end - start).total_seconds() <= 31 * 86400 + 3600
        ):
            raise Rejected("invalid_arguments")
        return (
            "calendar",
            "get_events",
            {"entity_id": target, "start_date_time": args["start"], "end_date_time": args["end"]},
            True,
        )
    if operation.startswith("problem_notice_"):
        if not re.fullmatch(r"wuschel_[0-9a-f-]{36}_(signal|configuration)", target):
            raise Rejected("not_allowed")
        if operation == "problem_notice_remove":
            if args:
                raise Rejected("invalid_arguments")
            return "persistent_notification", "dismiss", {"notification_id": target}, False
        if set(args) != {"title", "message"} or any(
            not isinstance(value, str) or len(value) > limit
            for value, limit in ((args["title"], 128), (args["message"], 8192))
        ):
            raise Rejected("invalid_arguments")
        return "persistent_notification", "create", {"notification_id": target, **args}, False
    profile = args.get("profile")
    if profile not in {"entity_poll", "companion_location"}:
        raise Rejected("not_allowed")
    expected = (
        {"profile", "permission_confirmed"} if profile == "companion_location" else {"profile"}
    )
    if (
        set(args) != expected
        or profile == "companion_location"
        and (
            args.get("permission_confirmed") is not True or not target.startswith("device_tracker.")
        )
    ):
        raise Rejected("not_allowed")
    if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", target):
        raise Rejected("not_allowed")
    # Resolve and validate the actual integration/entity in HA, never trust this profile alone.
    return (
        "refresh",
        profile,
        {
            "entity_id": target,
            **({"permission_confirmed": True} if profile == "companion_location" else {}),
        },
        False,
    )


class ServiceChannel:
    """One connection, four running + sixteen waiting, short-lived idempotency cache."""

    def __init__(self, execute, *, timeout_s=10, now=None, installation_id=None):
        self.execute = execute
        self.timeout_s = timeout_s
        self.now = now or asyncio.get_running_loop().time
        self.semaphore = asyncio.Semaphore(4)
        self.pending = {}
        self.cache = OrderedDict()
        self.closed = False
        self.installation_id = str(UUID(installation_id)) if installation_id else None

    def close(self):
        self.closed = True
        self.cache.clear()

    async def call(self, request):
        request_id = request.get("request_id")

        def result(outcome, **values):
            return {"request_id": request_id, "outcome": outcome, **values}

        try:
            signature = json.dumps(request, sort_keys=True, allow_nan=False, separators=(",", ":"))
            if len(signature.encode()) > 65536:
                raise Rejected("invalid_arguments")
            mapped = action(request)
            if request["operation"].startswith("problem_notice_") and request["target_ids"][
                0
            ] not in {
                f"wuschel_{self.installation_id}_signal",
                f"wuschel_{self.installation_id}_configuration",
            }:
                raise Rejected("not_allowed")
        except Rejected as error:
            return result("rejected", error_code=str(error))
        except KeyError, TypeError, ValueError, OverflowError:
            return result("rejected", error_code="invalid_arguments")
        if self.closed:
            return result("rejected", error_code="ha_unavailable")
        for key, (_, expires, _) in tuple(self.cache.items()):
            if expires <= self.now():
                del self.cache[key]
        if request_id in self.cache:
            old_signature, _, response = self.cache[request_id]
            return (
                response
                if old_signature == signature
                else result("rejected", error_code="request_conflict")
            )
        if request_id in self.pending:
            old_signature, future = self.pending[request_id]
            if old_signature != signature:
                return result("rejected", error_code="request_conflict")
            return await asyncio.shield(future)
        if len(self.pending) >= 20:
            return result("rejected", error_code="busy")
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = signature, future
        try:
            async with self.semaphore:
                if self.closed:
                    response = result("rejected", error_code="ha_unavailable")
                else:
                    try:
                        async with asyncio.timeout(self.timeout_s):
                            data = await self.execute(*mapped)
                            if len(json.dumps(data, allow_nan=False).encode()) > 1048576:
                                raise Rejected("invalid_arguments")
                            response = result("completed", data=data)
                    except Rejected as error:
                        response = result("rejected", error_code=str(error))
                    except TimeoutError:
                        response = result("indeterminate", error_code="timeout")
                    except Exception:
                        # Provider text may contain private calendar data or credentials.
                        response = result("failed", error_code="ha_unavailable")
            if not self.closed:
                self.cache[request_id] = signature, self.now() + 300, response
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
            future.set_result(response)
            return response
        except asyncio.CancelledError:
            future.set_result(result("indeterminate", error_code="ha_unavailable"))
            raise
        finally:
            self.pending.pop(request_id, None)
