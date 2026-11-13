"""Operating file, startup fallback, bounds, and live diagnostic contracts."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from core_contracts.api import API
from core_contracts.operations import OperationsFile, OperationsSettings
from core_contracts.persistence import MemoryStore, PostgresStore
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens


@pytest.mark.parametrize("key", OperationsSettings.model_fields)
def test_every_setting_has_metadata_and_enforces_bounds(key):
    field = OperationsSettings.model_json_schema()["properties"][key]
    assert field["title"] and field["description"] and field["unit"]
    assert field["restart_required"] is True
    for value in (field["minimum"], field["maximum"]):
        OperationsSettings.model_validate({key: value})
    for value in (
        field["minimum"] - 1,
        field["maximum"] + 1,
        True,
        "10",
        float("nan"),
        float("inf"),
    ):
        with pytest.raises(ValidationError):
            OperationsSettings.model_validate({key: value})


@pytest.mark.parametrize(
    "contents,notice",
    [
        (None, "operations_missing"),
        ("broken", "operations_invalid"),
        ('{"ingest_queue_size":0}', "operations_invalid"),
        ('{"password":"secret"}', "operations_invalid"),
        ("[]", "operations_invalid"),
        ('{"ha_retry_max_s":NaN}', "operations_invalid"),
    ],
)
async def test_bad_file_falls_back_without_preventing_start(tmp_path, runtime, contents, notice):
    path = tmp_path / "operations.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    document = OperationsFile(path)
    assert document.active == OperationsSettings()
    assert document.notice == notice
    await runtime.start()
    assert runtime.persistence
    assert "secret" not in json.dumps(document.view())


def test_atomic_save_restart_and_resets(tmp_path, monkeypatch):
    path = tmp_path / "operations.json"
    document = OperationsFile(path)
    result = document.update({"ingest_queue_size": 7, "ha_retry_max_s": 3})
    changed = next(item for item in result["settings"] if item["key"] == "ingest_queue_size")
    assert changed["value"] == 7 and changed["active_value"] == 256 and changed["pending_restart"]
    restarted = OperationsFile(path)
    assert restarted.active.ingest_queue_size == 7
    assert all(not item["pending_restart"] for item in restarted.view()["settings"])
    previous = path.read_bytes()
    with monkeypatch.context() as patch:

        def failed(*args):
            raise OSError("private database URL must never leave the file layer")

        patch.setattr("core_contracts.operations.os.replace", failed)
        with pytest.raises(OSError):
            document.update({"ingest_queue_size": 9})
    assert path.read_bytes() == previous
    assert document.saved.ingest_queue_size == 7
    assert not list(tmp_path.glob(".operations-*"))
    document.reset("ingest_queue_size")
    assert document.saved.ingest_queue_size == 256 and document.saved.ha_retry_max_s == 3
    document.reset()
    assert document.saved == OperationsSettings()
    with pytest.raises(ValueError):
        document.reset("unknown")


async def test_database_settings_reach_writer_and_pool(tmp_path, monkeypatch):
    connection = AsyncMock()
    connection.fetchval.return_value = True
    connect, pool = AsyncMock(return_value=connection), AsyncMock()
    monkeypatch.setattr("core_contracts.persistence.asyncpg.connect", connect)
    monkeypatch.setattr("core_contracts.persistence.asyncpg.create_pool", pool)
    settings = OperationsSettings(
        database_command_timeout_s=4,
        database_connect_timeout_s=6,
        database_idle_session_timeout_s=80,
        database_pool_size=5,
    )
    store = PostgresStore("private", tmp_path, operations=settings)
    monkeypatch.setattr(store, "migrate", AsyncMock())
    await store.open()
    assert connect.call_args.kwargs["timeout"] == 6
    assert connect.call_args.kwargs["command_timeout"] == 4
    assert connect.call_args.kwargs["server_settings"]["idle_session_timeout"] == "80000"
    assert pool.call_args.kwargs["max_size"] == 5
    assert pool.call_args.kwargs["timeout"] == 6
    assert pool.call_args.kwargs["command_timeout"] == 4


async def test_operations_api_roles_validation_reset_and_system_state(
    runtime, aiohttp_client, tmp_path
):
    document = OperationsFile(tmp_path / "operations.json")
    api = API(runtime, Tokens(tmp_path / "secrets"), "test-installation", operations=document)
    client = await aiohttp_client(api.application())
    admin = {"Authorization": "Bearer " + api.tokens.values["admin"]}
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    for method, path, body in (
        ("GET", "operations/settings", None),
        ("PUT", "operations/settings", {}),
        ("POST", "operations/settings/reset", {}),
        ("GET", "system/state", None),
    ):
        response = await client.request(method, "/api/v1/" + path, json=body, headers=consumer)
        assert response.status == 403
    response = await client.get("/api/v1/operations/settings", headers=admin)
    assert response.status == 200
    assert (await response.json())["notice"] == "operations_missing"
    response = await client.put(
        "/api/v1/operations/settings", json={"ingest_queue_size": 0}, headers=admin
    )
    assert response.status == 400
    assert "Gib einen Wert" in await response.text()
    assert not document.path.exists()
    response = await client.put(
        "/api/v1/operations/settings", json={"password": "secret"}, headers=admin
    )
    assert response.status == 400 and "secret" not in await response.text()
    response = await client.put(
        "/api/v1/operations/settings", json={"ingest_queue_size": 8}, headers=admin
    )
    assert response.status == 200
    assert runtime.queue.maxsize == 256
    response = await client.post(
        "/api/v1/operations/settings/reset", json={"key": "ingest_queue_size"}, headers=admin
    )
    assert response.status == 200 and document.saved.ingest_queue_size == 256
    response = await client.post(
        "/api/v1/operations/settings/reset", json={"key": None}, headers=admin
    )
    assert response.status == 400
    runtime.retry_failed("ha", ConnectionError("private credential"))
    runtime.retry_succeeded("ha")
    runtime.commit_samples.clear()
    runtime.commit_samples.extend([1, 3, 2, 100])
    response = await client.get("/api/v1/system/state", headers=admin)
    result = await response.json()
    assert result["commits"]["median_ms"] == 2.5 and result["commits"]["p95_ms"] == 100
    assert result["event_loop"]["lag_ms"] is None
    assert result["retries"]["ha"]["count"] == 1 and result["retries"]["ha"]["consecutive"] == 0
    assert result["retries"]["ha"]["last_error"] == "ConnectionError"
    assert "private" not in json.dumps(result)
    assert "startup" in result and "startup_attempts" in result
    assert {"version", "publication_seq", "persistence", "ha", "bridge", "mqtt"} <= result.keys()


async def test_event_loop_meter_measures_lag_and_cancels(runtime, monkeypatch):
    now = [0.0]
    slept = asyncio.Event()
    calls = 0

    async def sleep(seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            now[0] += seconds + 0.025
        else:
            slept.set()
            await asyncio.Future()

    monkeypatch.setattr(runtime.clock, "sleep", sleep)
    monkeypatch.setattr(runtime.clock, "monotonic", lambda: now[0])
    task = asyncio.create_task(runtime.measure_event_loop())
    await slept.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.event_loop_lag_ms == 25 and runtime.event_loop_lag_max_ms == 25


def test_loaded_nondefault_values_size_running_queues_and_remain_until_restart(tmp_path, runtime):
    path = tmp_path / "operations.json"
    path.write_text(
        '{"ingest_queue_size":7,"refresh_queue_size":9,"subscriber_queue_size":11}',
        encoding="utf-8",
    )
    document = OperationsFile(path)
    running = Runtime(MemoryStore(), runtime.clock, runtime.types, operations=document.active)
    assert running.queue.maxsize == 7
    assert running.refresh_requests.maxsize == 9
    assert running.subscribe().maxsize == 11
    document.update({"ingest_queue_size": 8})
    assert running.queue.maxsize == 7
    assert OperationsFile(path).active.ingest_queue_size == 8


@pytest.mark.parametrize("kind", ["ha", "mqtt"])
async def test_custom_retry_caps_and_counters_are_applied(runtime, config, monkeypatch, kind):
    import aiohttp

    from core_contracts.adapters.ha import BridgeAdapter
    from core_contracts.adapters.mqtt import MQTTAdapter
    from tests.test_phase1a import activate

    await activate(runtime, config)
    runtime.operations = OperationsSettings(ha_retry_max_s=2, mqtt_retry_max_s=2)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 5:
            raise asyncio.CancelledError

    async def failed(*args):
        raise TimeoutError("sensitive driver error must not be exposed")

    monkeypatch.setattr(runtime.clock, "sleep", sleep)
    monkeypatch.setattr("core_contracts.adapters.ha.random.uniform", lambda low, high: high)
    async with aiohttp.ClientSession() as session:
        if kind == "ha":
            adapter = BridgeAdapter(runtime, session, "ws://unused", "test")
            monkeypatch.setattr(adapter, "connect", failed)
        else:
            adapter = MQTTAdapter(runtime, session, "external", {}, "")
            monkeypatch.setattr(adapter, "credentials", failed)
        with pytest.raises(asyncio.CancelledError):
            await adapter.run()
    assert delays == [1, 2, 2, 2, 2]
    assert runtime.retries[kind]["count"] == 5
    assert runtime.retries[kind]["last_error"] == "TimeoutError"
    assert "sensitive" not in json.dumps(runtime.system_state())


async def test_failed_file_write_is_safe_and_cross_site_writes_are_rejected(
    runtime, aiohttp_client, tmp_path, monkeypatch
):
    document = OperationsFile(tmp_path / "operations.json")
    api = API(
        runtime,
        Tokens(tmp_path / "secrets"),
        "test",
        operations=document,
        ingress_address="127.0.0.1",
    )
    client = await aiohttp_client(api.application(ingress=True))
    response = await client.put(
        "/api/v1/operations/settings",
        json={"ingest_queue_size": 8},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status == 403 and not document.path.exists()

    def failed(*args):
        raise OSError("private filesystem details")

    monkeypatch.setattr("core_contracts.operations.os.replace", failed)
    response = await client.put("/api/v1/operations/settings", json={"ingest_queue_size": 8})
    assert response.status == 503
    assert await response.json() == {"error": "operations_write_failed"}
    assert document.saved == OperationsSettings() and not document.path.exists()
