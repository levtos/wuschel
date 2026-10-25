"""Independent regressions for the pre-install review; no production HA access."""

import asyncio
import copy
import gc
import json
import tracemalloc
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import aiohttp
import pytest
from core_contracts_client import AuthenticationError, CoreContractsClient

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.api import API
from core_contracts.app import JsonFormatter, connect_with_health
from core_contracts.evidence import assess
from core_contracts.persistence import MemoryStore
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.registry import fingerprints, validate
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.temporal import Temporal, TemporalState

from .conftest import activate
from .test_runtime import obs


def field(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]


def temporal(config, operation="stable_for", duration=60):
    config["contracts"][0].update(
        type_id="test.temporal",
        parameters={"operation": operation, "duration_s": duration, "accepts_held": False},
    )


@pytest.mark.parametrize("operation", ["echo", "age", "edge", "grace", "report_heartbeat"])
async def test_idle_soak_has_constant_current_state_history_and_publications(
    runtime, config, operation
):
    if operation in {"age", "edge", "grace"}:
        temporal(config, operation)
    if operation == "report_heartbeat":
        config["sources"][0]["freshness"] = {
            "mode": "report_heartbeat",
            "future_tolerance_s": 1,
            "interval_s": 1_000_000,
        }
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    await runtime.submit("tick")
    before = runtime.state.model_dump_json()
    generation = runtime.state.generation
    history = len(runtime.store.state.tables["contract_state_history"])
    publications = len(runtime.store.state.publications)
    tracemalloc.start()
    for _ in range(100):
        runtime.clock.advance(60)
        await runtime.submit("tick")
    gc.collect()
    initial = tracemalloc.get_traced_memory()[0]
    for _ in range(10000):
        runtime.clock.advance(60)
        await runtime.submit("tick")
    gc.collect()
    growth = tracemalloc.get_traced_memory()[0] - initial
    tracemalloc.stop()
    assert growth < 100_000
    assert runtime.state.model_dump_json() == before
    assert runtime.state.generation == generation
    assert len(runtime.store.state.tables["contract_state_history"]) == history
    assert len(runtime.store.state.publications) == publications
    assert not runtime.state.publications
    assert not runtime.state.tables["contract_state_history"]


async def test_tick_publishes_real_freshness_and_due_temporal_changes(runtime, config):
    temporal(config, duration=2)
    config["sources"][0]["freshness"] = {
        "mode": "periodic_ttl",
        "future_tolerance_s": 1,
        "interval_s": 5,
    }
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    assert field(runtime)["value"] is False
    seq = runtime.state.publication_seq
    runtime.clock.advance(2)
    await runtime.submit("tick")
    assert field(runtime)["value"] is True
    assert runtime.state.publication_seq == seq + 1
    runtime.clock.advance(4)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "unknown"
    seq = runtime.state.publication_seq
    for _ in range(10):
        runtime.clock.advance(1)
        await runtime.submit("tick")
    assert runtime.state.publication_seq == seq


@pytest.mark.parametrize(
    "flags,reason",
    [
        ({"ha_restored": True}, "input_restored"),
        ({"ha_restored": True, "availability": "unavailable"}, "input_unavailable"),
        ({"assumed_state": True}, "input_unknown"),
    ],
)
async def test_negative_quality_transition_is_never_dropped(runtime, config, flags, reason):
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    await runtime.submit("observation", obs(runtime, **flags))
    assert field(runtime)["status"] == "unknown"
    assert field(runtime)["reasons"][0]["code"] == reason
    runtime.clock.advance(1)
    await runtime.submit("observation", obs(runtime))
    assert field(runtime)["status"] == "valid"


async def test_gap_is_transition_only_and_mqtt_does_not_reset_ha_anchor(runtime, config):
    temporal(config, duration=10)
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    anchor = runtime.state.tables["node_state"]["fixture.echo"]["temporal"]["since_at"]
    await runtime.submit("mqtt_state", "connected")
    for _ in range(10):
        runtime.clock.advance(2)
        await runtime.submit("mqtt_state", "disconnected")
        await runtime.submit("tick")
    assert field(runtime)["value"] is True
    assert runtime.state.tables["node_state"]["fixture.echo"]["temporal"]["since_at"] == anchor
    assert len(await runtime.store.rows("history_gap")) == 1
    for _ in range(10):
        await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    assert len(await runtime.store.rows("history_gap")) == 2


@pytest.mark.parametrize("restart", [False, True])
async def test_grace_cannot_rearm_from_pre_gap_evidence(runtime, config, restart):
    temporal(config, "grace")
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    if restart:
        await runtime.stop()
        runtime = Runtime(runtime.store, runtime.clock, runtime.types)
        await runtime.start()
    else:
        await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    try:
        runtime.clock.advance(3600)
        await runtime.submit("observation", obs(runtime, None, availability="unknown"))
        assert field(runtime)["status"] == "unknown"
        assert field(runtime)["held_until"] is None
    finally:
        if restart:
            await runtime.stop()


def test_not_applicable_never_uses_grace(runtime):
    now = runtime.clock.now_utc()
    tool = Temporal(TemporalState(fingerprint="x"))
    tool.grace(FieldValue(status="valid", quality="healthy", value=True), 10, now, trigger=True)
    na = FieldValue(status="not_applicable", quality="healthy", positively_not_applicable=True)
    assert tool.grace(na, 10, now, trigger=True) == na
    assert (
        tool.grace(unknown(ReasonCode.INPUT_UNKNOWN, "a", now), 10, now, trigger=True).status
        == "unknown"
    )


@pytest.mark.parametrize("operation", ["validate", "duplicate", "conflict"])
async def test_overflow_transaction_survives_control_requests(runtime, config, operation):
    temporal(config)
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    runtime.clock.advance(50)
    runtime.overflow = True
    runtime.latest["binding.a"] = obs(runtime)
    if operation == "validate":
        await runtime.submit("validate", config)
    elif operation == "duplicate":
        await runtime.submit("observation", obs(runtime))
    else:
        with pytest.raises(ValueError):
            await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 0})
    assert not runtime.overflow and not runtime.latest
    assert any(g["reason"] == "ingest_overflow" for g in await runtime.store.rows("history_gap"))
    runtime.clock.advance(11)
    await runtime.submit("tick")
    assert field(runtime)["value"] is False
    runtime.clock.advance(49)
    await runtime.submit("tick")
    assert field(runtime)["value"] is True


async def test_real_queuefull_lost_intermediate_invalidates_continuity(runtime, config):
    other = Runtime(MemoryStore(), runtime.clock, runtime.types, queue_size=1)
    await other.start()
    try:
        temporal(config)
        await activate(other, config)
        await other.submit("observation", obs(other))
        other.clock.advance(50)
        other.ingest(obs(other))
        other.ingest(obs(other, False))
        other.ingest(obs(other, True))
        assert other.overflow
        await other.submit("tick")
        other.clock.advance(11)
        await other.submit("tick")
        assert field(other)["value"] is False
        assert await other.store.rows("history_gap")
    finally:
        await other.stop()


async def test_failed_overflow_commit_keeps_buffer(runtime, config):
    await activate(runtime, config)
    runtime.latest["binding.a"] = obs(runtime)
    runtime.overflow = True
    runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("injected"))
    with pytest.raises(OSError):
        await runtime.submit("validate", config)
    assert runtime.latest and runtime.overflow
    runtime.store.before_commit = None
    await runtime.submit("recover")
    assert field(runtime)["value"] is True


async def test_new_overflow_during_commit_is_not_cleared(runtime, config, monkeypatch):
    await activate(runtime, config)
    runtime.latest["binding.a"] = obs(runtime, False)
    runtime.overflow = True
    commit = runtime.store.commit
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def delayed_commit(state):
        nonlocal calls
        index = calls
        calls += 1
        if index < 2:
            entered[index].set()
            await release[index].wait()
        await commit(state)

    monkeypatch.setattr(runtime.store, "commit", delayed_commit)
    request = asyncio.create_task(runtime.submit("validate", config))
    try:
        async with asyncio.timeout(5):
            await entered[0].wait()
            runtime.clock.advance(1)
            for _ in range(runtime.queue.maxsize):
                runtime.ingest(obs(runtime, False))
            runtime.clock.advance(1)
            newest = obs(runtime, True)
            runtime.ingest(newest)
            release[0].set()
            await request
            await entered[1].wait()
            assert runtime.overflow and runtime.latest["binding.a"] is newest
            release[1].set()
            await runtime.queue.join()
        assert not runtime.overflow and not runtime.latest
        assert field(runtime)["value"] is True
        gaps = await runtime.store.rows("history_gap")
        assert sum(gap["reason"] == "ingest_overflow" for gap in gaps) == 2
    finally:
        for event in release:
            event.set()
        await request


@pytest.mark.parametrize(
    "case", ["disabled", "incompatible", "missing", "stale", "partial", "corrupt"]
)
async def test_machine_has_declared_fresh_evidence_reinitialization(runtime, config, case):
    config["contracts"][0].update(
        type_id="test.state_machine",
        parameters={"deadline_s": 10, "sessions": False},
        enabled=case != "disabled",
    )
    await activate(runtime, config)
    for _ in range(3):
        await runtime.submit("tick")
    if case == "disabled":
        assert not runtime.state.tables["contract_lifecycle"]["fixture.echo"]["ever_active"]
        config["contracts"][0]["enabled"] = True
        await activate(runtime, config)
    elif case == "incompatible":
        config["contracts"][0]["parameters"]["deadline_s"] = 20
        await activate(runtime, config)
    elif case == "missing":
        runtime.state.tables["node_state"].pop("fixture.echo")
    elif case == "partial":
        runtime.state.tables["node_state"]["fixture.echo"]["machine"] = {}
    elif case == "corrupt":
        runtime.state.tables["node_state"]["fixture.echo"]["machine"]["state"] = "invalid"
    else:
        config["contracts"][0]["enabled"] = False
        await activate(runtime, config)
        config["contracts"][0]["enabled"] = True
        await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    assert field(runtime)["status"] == "valid"
    assert field(runtime)["value"] == "a"


async def test_initial_deadline_without_transition_is_processed_once(runtime, config):
    config["contracts"][0].update(
        type_id="test.state_machine", parameters={"deadline_s": 1, "sessions": False}
    )
    await activate(runtime, config)
    runtime.clock.advance(2)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "valid"
    assert runtime.state.tables["sm_instance"]["fixture.echo"]["deadline"]["fired"]
    seq = runtime.state.publication_seq
    await runtime.submit("tick")
    assert runtime.state.publication_seq == seq


async def test_attribute_has_no_entity_state_continuity_proof(runtime, config):
    config["bindings"][0]["adapter"].update(kind="ha_attribute", ha_attribute="reading")
    configuration = validate(config, runtime.types)
    observation = obs(
        runtime, ha_last_changed=runtime.clock.now_utc() - timedelta(hours=1)
    ).model_copy(update={"adapter": "ha_attribute"})
    assert (
        assess(
            observation, configuration.sources[0].freshness, runtime.clock.now_utc(), "source.a"
        ).since_at
        is None
    )


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-01-01T12:00:00", True, "999999999999"])
async def test_malformed_external_timestamp_is_local_invalid_evidence(runtime, config, timestamp):
    config["bindings"][0]["adapter"]["ha_time_attribute"] = "device_timestamp"
    configuration = validate(config, runtime.types)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused", "test")
        observation = adapter.observation(
            configuration.bindings[0],
            {"state": True, "attributes": {"device_timestamp": timestamp}},
            "live_change",
            {},
            configuration,
        )
    assert observation.invalid_timestamp
    assert (
        assess(observation, configuration.sources[0].freshness, runtime.clock.now_utc(), "source.a")
        .reasons[0]
        .code
        == ReasonCode.INVALID_VALUE
    )


async def test_large_snapshot_is_one_work_item(runtime, config):
    for index in range(600):
        source = copy.deepcopy(config["sources"][0])
        source.update(source_id=f"source.x{index}", physical_source_key=f"physical.x{index}")
        config["sources"].append(source)
        config["bindings"].append(
            {
                "binding_id": f"binding.x{index}",
                "source_id": source["source_id"],
                "adapter": {"kind": "ha_state", "ha_entity_id": f"sensor.x{index}"},
                "transform": {"kind": "type_cast", "target": "bool"},
            }
        )
    await activate(runtime, config)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused", "test")
        await adapter.event({"kind": "snapshot", "states": {}}, runtime.config)
        assert runtime.queue.qsize() == 1
    await runtime.queue.join()
    assert runtime.first_snapshot
    assert len(runtime.state.tables["source_observation_current"]) == 601
    assert not runtime.overflow


async def test_recover_requires_new_snapshot(runtime, config):
    await activate(runtime, config)
    await runtime.submit("snapshot_complete")
    assert runtime.ready
    await runtime.submit("recover")
    assert not runtime.ready and not runtime.first_snapshot


async def test_archive_queries_order_and_limit(runtime, config):
    await activate(runtime, config)
    for number in range(120):
        runtime.clock.advance(1)
        await runtime.submit("observation", obs(runtime, number))
    rows = await runtime.store.rows("contract_state_history", contract_id="fixture.echo")
    assert len(rows) == 100
    assert [row["publication_seq"] for row in rows] == sorted(
        [row["publication_seq"] for row in rows], reverse=True
    )
    assert rows[0]["fields"]["value"]["value"] == 119
    assert not (await runtime.store.load()).tables["contract_state_history"]


def test_duplicate_adapter_and_unrelated_catalog_fingerprint(config):
    configuration = validate(
        config,
        __import__(
            "core_contracts.testing.contract_types", fromlist=["type_registry"]
        ).type_registry(),
    )
    from core_contracts.testing.contract_types import type_registry

    old = fingerprints(configuration, type_registry())
    config["catalogs"].append(
        {"catalog_id": "unused", "kind": "map", "version": 1, "entries": {"on": True}}
    )
    assert fingerprints(validate(config, type_registry()), type_registry()) == old
    source = copy.deepcopy(config["sources"][0])
    source.update(source_id="source.duplicate", physical_source_key="different")
    config["sources"].append(source)
    binding = copy.deepcopy(config["bindings"][0])
    binding.update(source_id="source.duplicate", binding_id="duplicate")
    config["bindings"].append(binding)
    with pytest.raises(ValueError, match="duplicate adapter"):
        validate(config, type_registry())


async def test_unauthorized_rate_limit_does_not_hide_health(runtime, aiohttp_client, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    client = await aiohttp_client(api.application())
    codes = [(await client.get("/api/v1/info")).status for _ in range(121)]
    assert 401 in codes and 429 in codes
    assert (await client.get("/health/live")).status == 200


async def test_websockets_close_within_shutdown_budget(runtime, aiohttp_client, tmp_path):
    tokens = Tokens(tmp_path)
    api = API(runtime, tokens, str(uuid4()))
    client = await aiohttp_client(api.application())
    socket = await client.ws_connect(
        "/api/v1/ws", headers={"Authorization": "Bearer " + tokens.values["consumer"]}
    )
    await socket.send_json({"type": "hello", "protocol_version": 1})
    await socket.receive_json()
    await socket.receive_json()
    await socket.send_json({"type": "subscribe"})
    await socket.receive_json()
    async with asyncio.timeout(4):
        shutdown = asyncio.create_task(api.close_sockets(client.app))
        assert (await socket.receive_json())["type"] == "going_away"
        await socket.receive()
        await shutdown
    assert socket.closed


async def test_client_auth_failure_is_terminal(runtime, aiohttp_server, tmp_path):
    identity = str(uuid4())
    tokens = Tokens(tmp_path)
    server = await aiohttp_server(API(runtime, tokens, identity).application())
    async with CoreContractsClient(
        str(server.make_url("")), tokens.values["consumer"], identity
    ) as client:
        tokens.rotate("consumer")
        with pytest.raises(AuthenticationError):
            await anext(client.subscribe())
        assert client.connection_state == "authentication_failed"


@pytest.mark.parametrize("adapter_kind", ["ha", "mqtt", "db"])
async def test_connection_exceptions_use_capped_backoff(runtime, config, monkeypatch, adapter_kind):
    await activate(runtime, config)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime.clock, "sleep", sleep)

    async def failed(*args):
        raise TimeoutError("synthetic timeout")

    async with aiohttp.ClientSession() as session:
        if adapter_kind == "ha":
            adapter = BridgeAdapter(runtime, session, "ws://unused", "test")
            monkeypatch.setattr(adapter, "connect", failed)
            run = adapter.run()
        elif adapter_kind == "mqtt":
            adapter = MQTTAdapter(runtime, session, "external", {}, "")
            monkeypatch.setattr(adapter, "credentials", failed)
            run = adapter.run()
        else:

            async def close():
                pass

            run = connect_with_health(SimpleNamespace(open=failed, close=close), runtime.clock)
        with pytest.raises(asyncio.CancelledError):
            await run
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert len(await runtime.store.rows("history_gap")) <= 1


def test_sanitized_logging_retains_context():
    import logging

    record = logging.LogRecord("test", logging.WARNING, "test", 1, "failure", (), None)
    record.error_type = "TimeoutError"
    record.operation = "activate"
    record.secret = "do-not-render"
    data = json.loads(JsonFormatter().format(record))
    assert data["error_type"] == "TimeoutError" and data["operation"] == "activate"
    assert "do-not-render" not in json.dumps(data)


async def test_live_database_rollback_does_not_reuse_sequence(runtime, config):
    await activate(runtime, config)
    backup = runtime.store.state.model_copy(deep=True)
    await runtime.submit("observation", obs(runtime))
    old_sequence = runtime.state.publication_seq
    runtime.store.state = backup
    await runtime.submit("recover")
    assert runtime.state.publication_seq > old_sequence
    assert any(g["reason"] == "database_rollback" for g in await runtime.store.rows("history_gap"))


async def test_client_malformed_websocket_resynchronizes(aiohttp_server, monkeypatch):
    import core_contracts_client as client_module
    from aiohttp import web

    connections = 0
    delays = []

    async def delay(seconds):
        delays.append(seconds)

    monkeypatch.setattr(client_module, "asyncio", SimpleNamespace(sleep=delay))
    identity = str(uuid4())

    async def info(request):
        return web.json_response({"installation_id": identity})

    async def websocket(request):
        nonlocal connections
        connections += 1
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.receive_json()
        await socket.send_json({"type": "welcome", "installation_id": identity})
        await socket.receive_json()
        if connections == 1:
            await socket.send_str("{broken-json")
        else:
            await socket.send_json(
                {"type": "snapshot", "epoch_id": "new", "publication_seq": 7, "contracts": {}}
            )
        await socket.close()
        return socket

    app = web.Application()
    app.router.add_get("/api/v1/info", info)
    app.router.add_get("/api/v1/ws", websocket)
    server = await aiohttp_server(app)
    async with CoreContractsClient(str(server.make_url("")), "test", identity) as client:
        stream = client.subscribe()
        async with asyncio.timeout(3):
            event = await anext(stream)
        assert event["type"] == "snapshot" and event["publication_seq"] == 7
        assert connections == 2 and len(delays) == 1
        await stream.aclose()


async def test_lost_command_response_is_recovered_without_double_execution(
    runtime, config, aiohttp_server
):
    from aiohttp import web

    config["contracts"][0].update(
        type_id="test.state_machine", parameters={"deadline_s": 60, "sessions": False}
    )
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    identity = str(uuid4())
    posts = 0

    async def info(request):
        return web.json_response({"installation_id": identity})

    async def command(request):
        nonlocal posts
        posts += 1
        await runtime.submit("command", await request.json())
        request.transport.close()
        return web.Response()

    async def stored(request):
        return web.json_response((await runtime.store.get("command_log", "lost"))["result"])

    app = web.Application()
    app.router.add_get("/api/v1/info", info)
    app.router.add_post("/api/v1/commands", command)
    app.router.add_get("/api/v1/commands/lost", stored)
    server = await aiohttp_server(app)
    async with CoreContractsClient(str(server.make_url("")), "test", identity) as client:
        result = await client.command(
            {
                "command_id": "lost",
                "contract_id": "fixture.echo",
                "command": "request_b",
                "args": {},
                "origin": {"kind": "test", "actor": "test", "client_name": "test"},
                "issued_at": runtime.clock.now_utc().isoformat(),
                "valid_until": (runtime.clock.now_utc() + timedelta(seconds=30)).isoformat(),
            }
        )
    assert result["status"] == "accepted" and posts == 1
    assert field(runtime)["value"] == "b"


@pytest.mark.parametrize("operation", ["stable_for", "grace"])
async def test_runtime_restart_restores_only_proven_anchor_or_remaining_grace(
    runtime, config, operation
):
    temporal(config, operation, 60)
    await activate(runtime, config)
    original_time = runtime.clock.now_utc()
    await runtime.submit(
        "observation", obs(runtime, ha_last_changed=original_time - timedelta(seconds=1))
    )
    if operation == "grace":
        runtime.clock.advance(1)
        await runtime.submit("observation", obs(runtime, availability="unavailable"))
        deadline = field(runtime)["held_until"]
    await runtime.stop()
    runtime.clock.advance(30)
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        if operation == "grace":
            assert field(restarted)["status"] == "held"
            assert field(restarted)["held_until"] == deadline
            restarted.clock.advance(31)
            await restarted.submit("tick")
            assert field(restarted)["status"] == "unknown"
        else:
            await restarted.submit(
                "observation",
                obs(restarted, ha_last_changed=original_time - timedelta(seconds=1)).model_copy(
                    update={"observation_kind": "snapshot"}
                ),
            )
            assert restarted.state.tables["node_state"]["fixture.echo"]["temporal"][
                "since_at"
            ] == original_time.isoformat().replace("+00:00", "Z")
            restarted.clock.advance(30)
            await restarted.submit("tick")
            assert field(restarted)["value"] is True
    finally:
        await restarted.stop()


def test_operator_and_fusion_preserve_affected_source_reason(runtime):
    from core_contracts.fusion import Fusion
    from core_contracts.resolver import boolean

    now = runtime.clock.now_utc()
    missing = unknown(ReasonCode.INPUT_UNAVAILABLE, "source.affected", now)
    for value in (
        boolean("not", [missing], now),
        Fusion().evaluate("first_healthy", [missing], now),
    ):
        assert any(
            r.input == "source.affected" and r.code == ReasonCode.INPUT_UNAVAILABLE
            for r in value.reasons
        )


@pytest.mark.parametrize(
    "mode,reports",
    [
        ("event_stateful", []),
        ("report_heartbeat", ["sensor.example_1"]),
        ("periodic_ttl", ["sensor.example_1"]),
    ],
)
async def test_handshake_revision_wakeup_and_report_filter(
    runtime, config, aiohttp_server, monkeypatch, mode, reports
):
    from dev.fake_ha import FakeHA

    if mode != "event_stateful":
        config["sources"][0]["freshness"].update(mode=mode, interval_s=10)
    await activate(runtime, config)
    fake = FakeHA()
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(
            runtime, session, str(server.make_url("/websocket")).replace("http:", "ws:"), "test"
        )
        original = adapter._result

        async def request(socket, document):
            result = await original(socket, document)
            if document["type"] == "get_config":
                config["contracts"][0]["display_name"] = "new revision during handshake"
                await activate(runtime, config)
            return result

        monkeypatch.setattr(adapter, "_result", request)
        async with asyncio.timeout(3):
            await adapter.connect(runtime.config)
    subscription = next(item for item in fake.requests if item["type"] == "traber/subscribe")
    assert subscription["report_entity_ids"] == reports
    assert runtime.revision_changed.is_set()
    assert runtime.mqtt_revision_changed.is_set()
    assert not runtime.first_snapshot


async def test_mqtt_internal_queuefull_invalidates_only_mqtt_bindings(runtime, config):
    config["bindings"][0]["adapter"] = {"kind": "mqtt", "mqtt_topic": "test/input"}
    await activate(runtime, config)
    async with aiohttp.ClientSession() as session:
        adapter = MQTTAdapter(runtime, session, "disabled", {}, "")
        queue = adapter.queue_type(1)
        queue.put_nowait(None)
        with pytest.raises(asyncio.QueueFull):
            queue.put_nowait(None)
        assert runtime.overflow_bindings == {"binding.a"}
        await runtime.submit("tick")
        assert len(await runtime.store.rows("history_gap")) == 1


async def test_safe_admin_validation_details(runtime, config, aiohttp_client, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    client = await aiohttp_client(api.application())
    headers = {"X-Authoring-Protocol": "2", "Authorization": "Bearer " + api.tokens.values["admin"]}
    config["contracts"][0].update(type_id="test.state_machine", parameters={"sessions": False})
    draft = await (
        await client.post(
            "/api/v1/registry/drafts",
            json={"config": config, "expected_active_revision": 0},
            headers=headers,
        )
    ).json()
    response = await client.post(
        f"/api/v1/registry/drafts/{draft['draft_id']}/validate", json={}, headers=headers
    )
    result = await response.json()
    assert response.status == 400
    assert result["details"] == [{"location": ["deadline_s"], "type": "missing"}]
    assert "input_value" not in json.dumps(result) and "ctx" not in json.dumps(result)


async def test_idle_without_registry_does_not_commit(runtime):
    generation = runtime.state.generation
    for _ in range(100):
        runtime.clock.advance(1)
        await runtime.submit("tick")
    assert runtime.state.generation == generation


async def test_source_future_clock_and_backward_jump_are_rechecked(runtime, config):
    await activate(runtime, config)
    await runtime.submit(
        "observation", obs(runtime, device_time=runtime.clock.now_utc() + timedelta(seconds=10))
    )
    assert field(runtime)["status"] == "unknown"
    runtime.clock.advance(9)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "valid"
    runtime.clock.jump(-5)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "unknown"
