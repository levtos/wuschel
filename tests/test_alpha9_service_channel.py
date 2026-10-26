"""Transport failure semantics without importing the HA runtime."""

import asyncio
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "traber_service_channel",
    Path(__file__).parents[1] / "custom_components/traber/service_channel.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ServiceChannel = module.ServiceChannel


def request(**changes):
    return {
        "request_id": str(uuid4()),
        "operation": "calendar_events",
        "target_ids": ["calendar.example"],
        "arguments": {"start": "2026-10-24T00:00:00+02:00", "end": "2026-10-26T00:00:00+01:00"},
        **changes,
    }


async def test_calendar_response_replay_conflict_expiry_and_connection_boundary():
    calls = []
    now = [0]

    async def execute(*args):
        calls.append(args)
        return {"calendar.example": {"events": []}}

    channel = ServiceChannel(execute, now=lambda: now[0])
    msg = request()
    result = await channel.call(msg)
    assert result["outcome"] == "completed"
    assert calls == [
        (
            "calendar",
            "get_events",
            {
                "entity_id": "calendar.example",
                "start_date_time": msg["arguments"]["start"],
                "end_date_time": msg["arguments"]["end"],
            },
            True,
        )
    ]
    assert await channel.call(msg) == result
    assert len(calls) == 1
    assert (await channel.call({**msg, "target_ids": ["calendar.other"]}))[
        "error_code"
    ] == "request_conflict"
    now[0] = 301
    assert (await channel.call(msg))["outcome"] == "completed"
    assert len(calls) == 2
    channel.close()
    assert (await channel.call(msg))["error_code"] == "ha_unavailable"


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "light.turn_on"},
        {"target_ids": ["all"]},
        {"target_ids": ["calendar.a", "calendar.b"]},
        {"arguments": {"start": "2026-01-01", "end": "2026-01-02"}},
        {"arguments": {"start": "2026-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"}},
        {"operation": "problem_notice_remove", "target_ids": ["foreign"], "arguments": {}},
        {
            "operation": "source_refresh",
            "target_ids": ["sensor.x"],
            "arguments": {"profile": "script"},
        },
        {"arguments": {"start": "x" * 66000, "end": "x"}},
        {"request_id": "not-a-uuid"},
        {
            "operation": "source_refresh",
            "target_ids": ["device_tracker.phone"],
            "arguments": {"profile": "companion_location"},
        },
        {
            "operation": "source_refresh",
            "target_ids": ["sensor.phone"],
            "arguments": {"profile": "companion_location", "permission_confirmed": True},
        },
        {
            "operation": "source_refresh",
            "target_ids": ["device_tracker.phone"],
            "arguments": {
                "profile": "companion_location",
                "permission_confirmed": True,
                "message": "arbitrary",
            },
        },
    ],
)
async def test_rejected_request_never_calls_ha(change):
    async def execute(*args):
        pytest.fail("Rejected request reached HA")

    assert (await ServiceChannel(execute).call(request(**change)))["outcome"] == "rejected"


async def test_timeout_is_indeterminate_and_not_retried():
    count = 0

    async def execute(*args):
        nonlocal count
        count += 1
        await asyncio.Event().wait()

    channel = ServiceChannel(execute, timeout_s=0.001)
    msg = request()
    result = await channel.call(msg)
    assert result["outcome"] == "indeterminate"
    assert await channel.call(msg) == result
    assert count == 1


async def test_concurrent_duplicates_and_queue_bound():
    release = asyncio.Event()
    entered = []

    async def execute(*args):
        entered.append(args)
        await release.wait()
        return None

    channel = ServiceChannel(execute)
    first = request()
    tasks = [asyncio.create_task(channel.call(first))]
    tasks += [asyncio.create_task(channel.call(request())) for _ in range(19)]
    await asyncio.sleep(0)  # noqa: TID251 -- yield to admitted tasks, no wall-clock delay
    duplicate = asyncio.create_task(channel.call(first))
    assert len(entered) == 4
    assert (await channel.call(request()))["error_code"] == "busy"
    release.set()
    results = await asyncio.gather(*tasks, duplicate)
    assert len(entered) == 20
    assert results[0] == results[-1]


async def test_own_notice_only_and_provider_errors_are_redacted():
    calls = []
    installation = str(uuid4())

    async def execute(*args):
        calls.append(args)
        raise RuntimeError("private event title and password")

    channel = ServiceChannel(execute, installation_id=installation)
    result = await channel.call(
        request(
            operation="problem_notice_put",
            target_ids=[f"wuschel_{installation}_configuration"],
            arguments={
                "title": "Konfiguration unvollständig",
                "message": "Ein Vertrag braucht eine Zuordnung.",
            },
        )
    )
    assert calls[0][:2] == ("persistent_notification", "create")
    assert result["error_code"] == "ha_unavailable"
    assert "private" not in str(result)
    rejected = await channel.call(
        request(
            operation="problem_notice_remove",
            target_ids=[f"wuschel_{uuid4()}_signal"],
            arguments={},
        )
    )
    assert rejected["error_code"] == "not_allowed"
    assert len(calls) == 1
