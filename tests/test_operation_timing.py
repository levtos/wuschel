import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from core_contracts import slots
from core_contracts.api import API, logical_route
from core_contracts.app import JsonFormatter
from core_contracts.observation_block import BlockRuntime
from core_contracts.operation_timing import ObservationTimings
from core_contracts.persistence import PostgresStore, State
from core_contracts.security import Tokens


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/api/v1/{path}", "registry/drafts", "registry/drafts"),
        ("/api/v1/{path}", "registry/drafts/private-id/activate", "registry/drafts/{id}/activate"),
        ("/api/v1/{path}", "registry/drafts/private-id/private-action", "{path}"),
        ("/api/v1/{path}", "history/query/observations", "history/query/observations"),
        ("/api/v1/{path}", "private-id", "{path}"),
        ("/api/v1/catalogs/care", "", "catalogs/care"),
    ],
)
def test_logical_route_only_uses_known_paths_and_patterns(pattern, path, expected):
    assert logical_route(pattern, path) == expected


def events(caplog, event):
    return [
        json.loads(JsonFormatter().format(record))
        for record in caplog.records
        if record.msg == event
    ]


async def test_queue_and_block_timings_are_debug_only(runtime, caplog, monkeypatch):
    async def process(operation, payload):
        await runtime.store.get("registry_revision", "synthetic-private-key")
        return payload

    async def process_observation(self, operation, payload):
        return await process(operation, payload)

    monkeypatch.setattr(runtime, "process", process)
    monkeypatch.setattr(BlockRuntime, "process", process_observation)

    async def run_work():
        assert await runtime.submit("synthetic_operation", "synthetic-private-value") == (
            "synthetic-private-value"
        )
        await runtime.submit("observation", "synthetic-private-value")
        await runtime.queue.join()

    with caplog.at_level(logging.INFO):
        await run_work()
        assert not events(caplog, "operation_timing")
    with caplog.at_level(logging.DEBUG):
        await run_work()

    queue, block = events(caplog, "operation_timing")
    assert queue["operation"] == "synthetic_operation"
    assert queue["db_calls"] == 1
    assert queue["queue_wait_ms"] >= 0
    assert queue["run_ms"] >= queue["db_ms"] >= 0
    assert block["operation"] == "observation_block"
    assert block["count"] == 1
    assert block["db_calls"] == 1
    assert block["run_ms"] >= 0
    assert queue["level"] == block["level"] == "DEBUG"
    assert not events(caplog, "observation_block")
    assert all("synthetic-private" not in str(record.__dict__) for record in caplog.records)
    assert "synthetic-private" not in json.dumps([queue, block])


def test_observation_timings_are_aggregated_at_debug_only(caplog):
    timings = ObservationTimings()
    with caplog.at_level(logging.INFO):
        timings.add(1, 5)
        timings.flush()
        assert not events(caplog, "observation_timing")
    with caplog.at_level(logging.DEBUG):
        timings.add(1, 10)
        timings.add(2, 20)
        assert not events(caplog, "observation_timing")
        timings.flush()
    (record,) = events(caplog, "observation_timing")
    assert record["level"] == "DEBUG"
    assert record["count"] == 2
    assert record["queue_wait_max_ms"] == 2
    assert record["run_max_ms"] == 20


async def test_postgres_commit_timing_is_debug_only_and_has_all_fields(
    caplog, monkeypatch, tmp_path
):
    store = PostgresStore("synthetic-private-dsn", tmp_path)
    monkeypatch.setattr(store, "probe", AsyncMock())
    monkeypatch.setattr(store, "put_metadata", AsyncMock())
    writer = MagicMock()
    writer.is_closed.return_value = False
    monkeypatch.setattr(store, "writer", writer)
    state = State()
    with caplog.at_level(logging.INFO):
        await store.commit(state)
        assert not events(caplog, "commit_timing")
    with caplog.at_level(logging.DEBUG):
        await store.commit(state)
    (record,) = events(caplog, "commit_timing")
    assert record == {"event": "commit_timing", "level": "DEBUG", **store.last_commit_timing}
    assert set(store.last_commit_timing) == {
        "commit_ms",
        "changes_ms",
        "sql_ms",
        "snapshot_ms",
        "written_rows",
        "clone_calls",
    }
    assert "synthetic-private" not in json.dumps(record)


async def test_load_change_reports_current_then_last_operation(
    runtime, caplog, monkeypatch, aiohttp_client, tmp_path
):
    tokens = Tokens(tmp_path)
    client = await aiohttp_client(API(runtime, tokens, "synthetic").application())
    response = await client.get(
        "/api/v1/registry/drafts?synthetic=private-query",
        headers={"Authorization": f"Bearer {tokens.values['admin']}"},
    )
    assert response.status == 200
    await response.read()
    entered, release = asyncio.Event(), asyncio.Event()

    async def process(operation, payload):
        entered.set()
        await release.wait()

    monkeypatch.setattr(runtime, "process", process)
    runtime.tick()
    await entered.wait()
    try:
        runtime.event_loop_lag_ms = 125.12345
        runtime.sample_load()
        runtime.clock.advance(runtime.operations.load_enter_hold_s)
        runtime.sample_load()
    finally:
        release.set()
        await runtime.queue.join()

    runtime.event_loop_lag_ms = 0
    runtime.sample_load()
    runtime.clock.advance(runtime.operations.load_recover_hold_s)
    runtime.sample_load()

    strained, normal = events(caplog, "load_state_changed")
    assert strained == {
        "event": "load_state_changed",
        "level": "WARNING",
        "previous_load_state": "normal",
        "load_state": "angespannt",
        "reasons": ["lag_ms"],
        "metrics": {
            "lag_ms": 125.123,
            "queue_percent": 0,
            "overflows_per_minute": 0,
            "commit_p95_ms": None,
        },
        "operation": "tick",
        "operation_active": True,
        "route": "registry/drafts",
        "request_active": False,
        "request_run_ms": 0.0,
        "run_ms": runtime.operations.load_enter_hold_s * 1000,
    }
    assert normal == {
        **strained,
        "previous_load_state": "angespannt",
        "load_state": "normal",
        "operation_active": False,
        "reasons": [],
        "metrics": {**strained["metrics"], "lag_ms": 0},
    }


async def test_loop_stall_is_logged_once_per_phase_with_safe_activity(
    runtime, caplog, monkeypatch, aiohttp_client, tmp_path
):
    route_entered, route_release = asyncio.Event(), asyncio.Event()
    samples = asyncio.Queue()

    async def sleep(seconds):
        assert seconds == 1
        sampled = asyncio.get_running_loop().create_future()
        samples.put_nowait(sampled)
        await sampled

    async def route(request):
        route_entered.set()
        await route_release.wait()
        return web.Response()

    def register(app, api):
        app.router.add_get("/api/v1/synthetic/{item}", route)

    async def process(operation, payload):
        runtime.clock.advance(0.375)

    monkeypatch.setattr(runtime.clock, "sleep", sleep)
    monkeypatch.setattr(runtime, "process", process)
    monkeypatch.setattr(slots, "ROUTES", [register, *slots.ROUTES])
    tokens = Tokens(tmp_path)
    tokens.values["admin"] = "synthetic-private-token"
    client = await aiohttp_client(API(runtime, tokens, "synthetic-installation").application())
    meter = asyncio.create_task(runtime.measure_event_loop())
    request = None
    try:
        sample = await samples.get()
        runtime.clock.advance(0.875)
        request = asyncio.create_task(
            client.get(
                "/api/v1/synthetic/synthetic-private-id?key=synthetic-private-query",
                headers={"Authorization": "Bearer synthetic-private-token"},
            )
        )
        await route_entered.wait()
        runtime.tick()
        await runtime.queue.join()  # The tick already ended when the meter resumes.
        sample.set_result(None)
        sample = await samples.get()
        route_release.set()
        response = await request
        assert response.status == 200

        runtime.clock.advance(1.125)
        sample.set_result(None)
        sample = await samples.get()
        assert not events(caplog, "event_loop_stall")

        runtime.clock.advance(1)
        sample.set_result(None)
        sample = await samples.get()
        (stall,) = events(caplog, "event_loop_stall")
        assert stall == {
            "event": "event_loop_stall",
            "level": "WARNING",
            "lag_ms": 250.0,
            "count": 2,
            "activities": [
                {"route": "synthetic/{item}", "run_ms": 375.0},
                {"operation": "tick", "run_ms": 375.0},
            ],
        }

        runtime.clock.advance(1 + runtime.operations.load_lag_ms_strained / 1000)
        sample.set_result(None)
        await samples.get()
    finally:
        route_release.set()
        if request is not None:
            await request
        meter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await meter

    first, second = events(caplog, "event_loop_stall")
    assert second == {
        **first,
        "lag_ms": runtime.operations.load_lag_ms_strained,
        "count": 1,
        "activities": [],
    }
    assert "synthetic-private" not in json.dumps([first, second])
