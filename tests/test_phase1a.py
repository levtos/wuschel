"""Permanent Phase-1A graph, schema, capability and lifecycle acceptance."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import aiohttp
import pytest
from core_contracts_client import CoreContractsClient

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.api import API
from core_contracts.app import diagnostic_options, prepare_setup
from core_contracts.producer import Producer
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.runtime import Runtime
from core_contracts.schema import FieldSchema, Schema, TypeRegistry, validate_fields
from core_contracts.security import Tokens
from core_contracts.testing.contract_types import type_registry
from core_contracts.testing.contract_types.evaluate import machine as machine_fields
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_core import NOW
from .test_runtime import obs


def add_source(config, name):
    source = copy.deepcopy(config["sources"][0])
    source.update(source_id=f"source.{name}", physical_source_key=f"physical.{name}")
    config["sources"].append(source)
    binding = copy.deepcopy(config["bindings"][0])
    binding.update(binding_id=f"binding.{name}", source_id=f"source.{name}")
    binding["adapter"]["ha_entity_id"] = f"input_boolean.cc_alpha_test_{name}"
    config["bindings"].append(binding)


def contract(name, kind, refs, parameters=None):
    return dict(
        contract_id=f"fixture.{name}",
        type_id=f"test.{kind}",
        type_version=1,
        enabled=True,
        inputs=[dict(name=key, ref=ref) for key, ref in zip("abcd", refs, strict=False)],
        parameters=parameters or {},
    )


def chain(config):
    add_source(config, "b")
    for binding in config["bindings"]:
        binding["transform"] = dict(kind="type_cast", target="bool")
    config["contracts"] = [
        contract(
            "grace",
            "temporal",
            ["source.a"],
            dict(operation="grace", duration_s=10, accepts_held=False),
        ),
        contract("not", "boolean", ["fixture.grace"], dict(operator="not")),
        contract("fusion", "fusion", ["fixture.not", "source.b"], dict(strategy="first_healthy")),
        contract(
            "stable",
            "temporal",
            ["fixture.fusion"],
            dict(operation="stable_for", duration_s=2, accepts_held=True),
        ),
    ]
    return config


def observation(runtime, value, name="a", **kwargs):
    return obs(runtime, value, **kwargs).model_copy(
        update={"source_id": f"source.{name}", "binding_id": f"binding.{name}"}
    )


def field(runtime, name="echo", key="value"):
    return runtime.snapshot()["contracts"][f"fixture.{name}"]["fields"][key]


async def exercise_chain(runtime, config):
    await activate(runtime, chain(config))
    await runtime.submit("observation", observation(runtime, True, "b"))
    await runtime.submit("observation", observation(runtime, False))
    runtime.clock.advance(2)
    await runtime.submit("tick")
    assert field(runtime, "stable")["value"] is True
    seq = runtime.state.publication_seq
    runtime.clock.advance(1)
    await runtime.submit("observation", observation(runtime, None, availability="unavailable"))
    assert runtime.state.publication_seq == seq + 1
    ends = {field(runtime, name)["held_until"] for name in ("grace", "not", "fusion", "stable")}
    assert len(ends) == 1 and None not in ends
    assert all(
        field(runtime, name)["status"] == "held" for name in ("grace", "not", "fusion", "stable")
    )
    return ends.pop()


async def test_multistage_publication_restore_and_no_grace_extension(runtime, config):
    end = await exercise_chain(runtime, config)
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        assert field(restarted, "grace")["held_until"] == end
        assert field(restarted, "not")["status"] == "held"
        restarted.clock.advance(11)
        await restarted.submit("tick")
        assert field(restarted, "grace")["status"] == "unknown"
        assert field(restarted, "stable")["status"] == "unknown"
        await restarted.submit("observation", observation(restarted, True, "b"))
        assert field(restarted, "fusion")["quality"] == "degraded"
        assert "partial_evidence" in {r["code"] for r in field(restarted, "fusion")["reasons"]}
        restarted.clock.advance(2)
        await restarted.submit("tick")
        assert field(restarted, "stable")["quality"] == "degraded"
    finally:
        await restarted.stop()


async def test_chain_revision_rollback_disabled_and_multistage_cycle(runtime, config):
    await exercise_chain(runtime, config)
    before = runtime.snapshot()
    broken = copy.deepcopy(config)
    broken["contracts"][0]["enabled"] = False
    with pytest.raises(ValueError, match="disabled required dependency"):
        await activate(runtime, broken)
    assert runtime.snapshot() == before
    cyclic = copy.deepcopy(config)
    cyclic["contracts"][0]["inputs"][0]["ref"] = "fixture.stable"
    with pytest.raises(ValueError, match="Kreis"):
        validate(cyclic, runtime.types)
    config["contracts"][1]["parameters"] = dict(operator="or")
    await activate(runtime, config)
    assert runtime.state.active_revision == 2
    assert all(c["registry_revision"] == 2 for c in runtime.snapshot()["contracts"].values())
    await runtime.submit("rollback", dict(revision=1, expected_active_revision=2))
    assert all(c["registry_revision"] == 3 for c in runtime.snapshot()["contracts"].values())


async def test_multifield_health_optional_and_downstream(runtime, config):
    add_source(config, "b")
    add_source(config, "c")
    config["contracts"] = [
        contract("multi", "multi", ["source.a", "source.b", "source.c"]),
        contract("echo", "echo", ["fixture.multi.flag"]),
    ]
    await activate(runtime, config)
    await runtime.submit("observation", observation(runtime, 42))
    await runtime.submit("observation", observation(runtime, True, "b"))
    item = runtime.snapshot()["contracts"]["fixture.multi"]
    assert item["contract_health"] == "healthy" and item["available"]
    assert item["fields"]["label"]["status"] == "unknown"
    assert field(runtime)["value"] is True
    await runtime.submit("observation", observation(runtime, "outside-catalog", "c"))
    assert field(runtime, "multi", "label")["reasons"][0]["code"] == "invalid_value"
    await runtime.submit("observation", observation(runtime, "a", "c"))
    assert field(runtime, "multi", "label")["value"] == "a"
    await runtime.submit("observation", observation(runtime, True))  # bool is not a number
    item = runtime.snapshot()["contracts"]["fixture.multi"]
    assert item["contract_health"] == "degraded" and item["available"] is False
    assert item["fields"]["value"]["value"] is None  # physical_state: reject, never fallback


@pytest.mark.parametrize(
    "kind,good,bad,catalog",
    [
        ("bool", True, 1, ()),
        ("number", 1.2, True, ()),
        ("number", 1, float("inf"), ()),
        ("text", "a", 1, ()),
        ("enum", "a", "c", ("a", "b")),
        ("timestamp", NOW, "not-a-time", ()),
        ("object", {}, None, ()),
    ],
)
def test_field_schemas(kind, good, bad, catalog):
    schema = FieldSchema(type=kind, required=True, physical_state=True, value_catalog=catalog)
    assert schema.accepts(good)
    assert not schema.accepts(bad)


def test_missing_required_schema_and_undeclared_grace():
    schema = Schema(fields={"value": FieldSchema(type="bool", required=True, physical_state=True)})
    assert validate_fields(schema, {}, NOW)["value"].reasons[0].code == ReasonCode.CONFIG_INCOMPLETE
    held = FieldValue(
        status="held",
        value=True,
        quality="healthy",
        held_until=NOW + timedelta(seconds=5),
        grace_declared=True,
        reasons=unknown(ReasonCode.GRACE, "a", NOW).reasons,
    )
    assert validate_fields(schema, {"value": held}, NOW)["value"].status == "unknown"
    assert validate_fields(schema, {"value": held}, NOW, {"a": held})["value"].status == "held"
    with pytest.raises(ValueError):
        Schema(fields=schema.fields, available_projection=("missing",))


def test_code_defined_type_registration_extensible_but_production_only_test():
    original = type_registry().get("test.echo", 1)
    registry = TypeRegistry()
    item = replace(
        original,
        type_id="example.extension",
        schema=original.schema.model_copy(update={"fixture": False}),
    )
    registry.register(item)
    assert registry.get("example.extension", 1) is item
    with pytest.raises(ValueError, match="immutable"):
        registry.register(item)
    assert all(
        name.startswith("test.") and t.schema.fixture
        for (name, _), t in type_registry().types.items()
    )


async def test_producer_lifecycle_transactional_disable_restore(runtime, config):
    def initialize(ctx):
        ctx.node["inits"] = ctx.node.get("inits", 0) + 1

    def dispose(ctx):
        ctx.node["disposed"] = True

    producer = Producer(lambda ctx: {"value": ctx.input("a")}, initialize, dispose)
    original = runtime.types.get("test.echo", 1)
    runtime.types.types["test.echo", 1] = replace(original, evaluate=producer)
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    assert runtime.state.tables["node_state"]["fixture.echo"]["inits"] == 1
    config["contracts"][0]["enabled"] = False
    await activate(runtime, config)
    assert runtime.state.tables["node_state"]["fixture.echo"]["disposed"]
    assert "producer_epoch" not in runtime.state.tables["node_state"]["fixture.echo"]
    config["contracts"][0]["enabled"] = True
    await activate(runtime, config)
    assert "disposed" not in runtime.state.tables["node_state"]["fixture.echo"]
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    assert restarted.state.tables["node_state"]["fixture.echo"]["inits"] == 2
    await restarted.stop()


@pytest.mark.parametrize("transport", ["ha", "mqtt"])
async def test_transforms_preserve_precise_failure(runtime, config, transport):
    config["catalogs"] = [dict(catalog_id="map.test", kind="map", version=1, entries={"on": True})]
    config["bindings"][0]["transform"] = dict(kind="map_ref", catalog_id="map.test")
    if transport == "mqtt":
        config["bindings"][0]["adapter"] = dict(
            kind="mqtt", mqtt_topic="test/alpha", mqtt_value_path="v", mqtt_time_path="at"
        )
    document = validate(config, runtime.types)
    await activate(runtime, config)
    async with aiohttp.ClientSession() as session:
        adapter = (
            BridgeAdapter(runtime, session, "ws://unused/websocket", "")
            if transport == "ha"
            else MQTTAdapter(runtime, session, "external", {}, "")
        )
        for value, expected in [("on", "valid"), ("missing", "unknown")]:
            runtime.clock.advance(1)
            if transport == "ha":
                item = adapter.observation(
                    document.bindings[0],
                    dict(state=value, last_changed=runtime.clock.now_utc().isoformat()),
                    "live_change",
                    {},
                    document,
                )
            else:
                item = adapter.observation(
                    document.bindings[0],
                    document,
                    json.dumps(dict(v=value, at=runtime.clock.now_utc().isoformat())).encode(),
                    False,
                    1,
                )
            await runtime.submit("observation", item)
            assert field(runtime)["status"] == expected
        assert field(runtime)["reasons"][0]["code"] == "unmapped_value"


async def test_dependency_internal_and_source_monitor(runtime, config):
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    assert field(runtime)["status"] == "valid"  # G17: event_stateful has no transport TTL.
    await runtime.submit("bridge_state", ("connected", "connected"))
    add_source(config, "b")
    config["dependencies"].append(
        dict(dependency_id="dep.monitored", kind="other", monitor="source", source_id="source.b")
    )
    config["sources"][0]["dependency_id"] = "dep.monitored"
    await activate(runtime, config)
    await runtime.submit("observation", observation(runtime, True))
    assert field(runtime)["status"] == "unknown"
    await runtime.submit("observation", observation(runtime, True, "b"))
    assert field(runtime)["status"] == "valid"
    await runtime.submit("observation", observation(runtime, False, "b"))
    assert field(runtime)["reasons"][0]["code"] == "dependency_unavailable"


def test_scheduler_and_unsupported_monitor_rejected(config):
    config["bindings"][0]["adapter"] = dict(kind="scheduler")
    with pytest.raises(ValueError, match="invalid configuration field"):
        validate(config, type_registry())
    config["bindings"][0]["adapter"] = dict(
        kind="ha_state", ha_entity_id="input_boolean.cc_alpha_test_a"
    )
    config["dependencies"][0]["monitor"] = "source"
    with pytest.raises(ValueError, match="monitor source required"):
        validate(config, type_registry())


@pytest.mark.parametrize(
    "args,status,reason",
    [
        ({"confirm": True}, "accepted", None),
        ({"confirm": False}, "rejected", "guard_rejected"),
        ({"confirm": "true"}, "rejected", "invalid_value"),
        ({}, "rejected", "invalid_value"),
        ({"confirm": True, "extra": 1}, "rejected", "invalid_value"),
    ],
)
async def test_typed_commands_and_idempotency(runtime, config, args, status, reason):
    config["contracts"] = [
        contract("echo", "state_machine", ["source.a"], dict(deadline_s=10, sessions=False))
    ]
    config["contracts"][0]["type_version"] = 2
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    command = dict(
        command_id="typed.1",
        contract_id="fixture.echo",
        command="request_b_if",
        args=args,
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
        expected_registry_revision=1,
    )
    result = await runtime.submit("command", command)
    assert result["status"] == status and result.get("reason") == reason
    seq = runtime.state.publication_seq
    assert await runtime.submit("command", command) == result
    assert runtime.state.publication_seq == seq
    conflict = await runtime.submit(
        "command", {**command, "command_id": "typed.2", "expected_registry_revision": 2}
    )
    assert conflict["reason"] == "active_revision_conflict"
    assert (await runtime.store.get("command_log", "typed.1"))["command"]["args"] == args


@pytest.mark.parametrize("ha_required", [False, True])
async def test_readiness_registry_dependencies(
    runtime, config, ha_required, aiohttp_client, tmp_path
):
    if not ha_required:
        config["bindings"][0]["adapter"] = dict(kind="mqtt", mqtt_topic="test/alpha")
        config["dependencies"][0]["kind"] = "mqtt"
    await activate(runtime, config)
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    client = await aiohttp_client(api.application())
    response = await client.get("/health/ready")
    assert response.status == (503 if ha_required else 200)
    assert ("first_bridge_snapshot_pending" in (await response.json())["reasons"]) == ha_required
    await runtime.submit(
        "snapshot", [obs(runtime).model_copy(update={"observation_kind": "snapshot"})]
    )
    assert runtime.ready
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    assert runtime.ready
    runtime._unavailable()
    assert not runtime.ready and runtime.live


async def test_refresh_capability_commit_and_worker(runtime, config, monkeypatch):
    def fields(ctx):
        if ctx.origin == "input":
            ctx.request_refresh("a")
        return {"value": ctx.input("a")}

    original = runtime.types.get("test.echo", 1)
    runtime.types.types["test.echo", 1] = replace(
        original, evaluate=Producer(fields), refresh_inputs=("a",)
    )
    await activate(runtime, config)
    assert runtime.refresh_requests.empty()
    await runtime.submit("observation", obs(runtime))
    called = []
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")

        async def refresh(source):
            called.append(source)

        monkeypatch.setattr(adapter, "request_refresh", refresh)
        worker = asyncio.create_task(adapter.refresh_worker())
        try:
            await asyncio.wait_for(runtime.refresh_requests.join(), 2)
            assert called == ["source.a"]
            runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("failure"))
            with pytest.raises(OSError):
                await runtime.submit("observation", obs(runtime, False))
            assert runtime.refresh_requests.empty()
            assert called == ["source.a"]
        finally:
            runtime.store.before_commit = None
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def test_graph_through_real_client(runtime, config, aiohttp_server, tmp_path):
    await exercise_chain(runtime, config)
    tokens = Tokens(tmp_path)
    identity = str(uuid4())
    server = await aiohttp_server(API(runtime, tokens, identity).application())
    async with CoreContractsClient(
        str(server.make_url("")), tokens.values["consumer"], identity
    ) as client:
        snapshot = await client.snapshot()
        assert set(snapshot.contracts) == {
            "fixture.grace",
            "fixture.not",
            "fixture.fusion",
            "fixture.stable",
        }
        stream = client.subscribe()
        await anext(stream)
        await anext(stream)
        async with aiohttp.ClientSession() as session:
            bridge = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
            item = bridge.observation(
                runtime.config.bindings[0],
                dict(state="on", last_changed=runtime.clock.now_utc().isoformat()),
                "live_change",
                {},
                runtime.config,
            )
            await runtime.submit("observation", item)
        publication = await anext(stream)
        assert publication["type"] == "delta"
        assert publication["prev_seq"] == snapshot.publication_seq
        assert publication["contracts"] == runtime.snapshot()["contracts"]
        assert (await runtime.store.load()).publication_seq == publication["publication_seq"]
        await stream.aclose()


@pytest.mark.parametrize(
    "change",
    [
        {"postgres_host": "bad host"},
        {"postgres_database": "postgres", "postgres_host": "db"},
        {"postgres_ca": "../private", "postgres_host": "db"},
    ],
)
async def test_invalid_supervisor_setup_blocks_writes_without_leaking(tmp_path, change):
    options = diagnostic_options().model_copy(update={**change, "postgres_user": "test"})
    setup = prepare_setup(options, tmp_path)
    assert setup.status()["error"] == "invalid_supervisor_options"
    assert setup.connection is None
    with pytest.raises(ValueError, match="invalid_supervisor_options"):
        await setup.dispatch("connect", "POST", {})
    assert not (tmp_path / "setup/connection.json").exists()


async def test_unused_ha_and_source_monitor_readiness(runtime, config):
    add_source(config, "b")
    config["bindings"][0]["adapter"] = dict(kind="mqtt", mqtt_topic="test/alpha")
    config["dependencies"].append(dict(dependency_id="dep.mqtt", kind="mqtt", monitor="internal"))
    config["sources"][0]["dependency_id"] = "dep.mqtt"
    await activate(runtime, config)
    assert runtime.ready and not runtime.requires_ha_snapshot  # unused HA binding
    config["dependencies"][1].update(monitor="source", source_id="source.b")
    await activate(runtime, config)
    assert runtime.requires_ha_snapshot and not runtime.ready


async def test_registered_refresh_type_only_once_per_negative_episode(runtime, config):
    config["contracts"][0]["type_version"] = 2
    await activate(runtime, config)
    assert runtime.refresh_requests.empty()
    for _ in range(3):
        runtime.clock.advance(1)
        await runtime.submit("observation", obs(runtime, None, availability="unavailable"))
    assert runtime.refresh_requests.qsize() == 1
    assert runtime.refresh_requests.get_nowait() == (1, "source.a")
    await runtime.submit("observation", obs(runtime, True))
    await runtime.submit("observation", obs(runtime, None, availability="unavailable"))
    assert runtime.refresh_requests.qsize() == 1


async def test_producer_failure_discards_node_and_refresh(runtime, config):
    def broken(ctx):
        ctx.node["partial"] = True
        ctx.request_refresh("a")
        raise ValueError("synthetic producer failure")

    original = runtime.types.get("test.echo", 1)
    runtime.types.types["test.echo", 1] = replace(
        original, evaluate=Producer(broken), refresh_inputs=("a",)
    )
    await activate(runtime, config)
    assert runtime.refresh_requests.empty()
    assert "partial" not in runtime.state.tables["node_state"]["fixture.echo"]
    assert field(runtime)["reasons"][0]["code"] == "evaluation_error"
    assert runtime.live


@pytest.mark.parametrize("fail_command", [True, False])
async def test_command_producer_failure_is_atomic_and_replayable(runtime, config, fail_command):
    def fields(ctx):
        result = machine_fields(ctx)  # state_machine() has already applied the override.
        if ctx.origin != "command":
            ctx.bucket(
                "command", FieldValue(status="valid", quality="healthy", value=0), False, 0, 1
            )
        if ctx.origin == "command":
            assert ctx.node["machine"]["state"] == "b"
            ctx.node["command_effect"] = True
            ctx.bucket(
                "command", FieldValue(status="valid", quality="healthy", value=2), False, 0, 1
            )
            ctx.request_refresh("a")
            if fail_command:
                raise ValueError("synthetic failure after state_machine")
        return result

    original = runtime.types.get("test.state_machine", 1)
    runtime.types.types["test.state_machine", 1] = replace(
        original, evaluate=Producer(fields), refresh_inputs=("a",)
    )
    config["contracts"] = [
        contract("echo", "state_machine", ["source.a"], dict(deadline_s=10, sessions=False))
    ]
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    before = runtime.state.model_copy(deep=True)
    latches = copy.deepcopy(runtime.latches)
    publications = runtime.subscribe()
    command = dict(
        command_id="producer.failure",
        contract_id="fixture.echo",
        command="request_b",
        args={},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
        expected_registry_revision=1,
    )
    expected = dict(command_id=command["command_id"], status="accepted")
    if fail_command:
        expected.update(status="rejected", reason="evaluation_error")

    # Inspect the actual commit boundary: response and effect must agree in one write.
    commit = runtime.store.commit
    committed = []

    async def inspect_commit(state):
        assert state.tables["command_log"][command["command_id"]]["result"] == expected
        if fail_command:
            for table in ("node_state", "sm_instance", "sm_episode", "sm_transition", "deadline"):
                assert state.tables[table] == before.tables[table]
            assert runtime.pending_latches == latches
            assert not runtime.pending_refresh
        else:
            assert state.tables["node_state"]["fixture.echo"]["machine"]["state"] == "b"
        committed.append(state.model_copy(deep=True))
        await commit(state)

    runtime.store.commit = inspect_commit
    try:
        result = await runtime.submit("command", command)
        assert result == expected
        assert len(committed) == 1
    finally:
        runtime.store.commit = commit
    assert (await runtime.store.get("command_log", command["command_id"]))["result"] == expected
    assert (await runtime.store.load()).tables["node_state"] == runtime.state.tables["node_state"]
    assert publications.get_nowait()["type"] == "delta"
    assert publications.empty()
    if fail_command:
        assert field(runtime)["reasons"][0]["code"] == "evaluation_error"
        assert runtime.latches == latches
        assert runtime.refresh_requests.empty()
    else:
        assert field(runtime)["value"] == "b"
        assert runtime.latches["fixture.echo:command"].latch is True
        assert runtime.refresh_requests.get_nowait() == (1, "source.a")
    snapshot = runtime.snapshot()
    assert await runtime.submit("command", command) == expected
    assert runtime.snapshot() == snapshot
    assert publications.empty() and runtime.refresh_requests.empty()
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        publications = restarted.subscribe()
        snapshot = restarted.snapshot()
        assert await restarted.submit("command", command) == expected
        assert restarted.snapshot() == snapshot
        assert publications.empty() and restarted.refresh_requests.empty()
    finally:
        await restarted.stop()


def lifecycle_producer(runtime):
    def initialize(ctx):
        ctx.node["inits"] = ctx.node.get("inits", 0) + 1

    def dispose(ctx):
        ctx.node["disposed"] = True

    hooks = Mock()
    hooks.init.side_effect = initialize
    hooks.dispose.side_effect = dispose
    original = runtime.types.get("test.boolean", 1)
    runtime.types.types["test.boolean", 1] = replace(
        original,
        evaluate=Producer(lambda ctx: {"value": ctx.input("a")}, hooks.init, hooks.dispose),
    )
    return hooks


@pytest.mark.parametrize("change", ["parameter", "binding"])
async def test_producer_lifecycle_semantic_change(runtime, config, change):
    hooks = lifecycle_producer(runtime)
    config["contracts"] = [contract("echo", "boolean", ["source.a"], dict(operator="not"))]
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    old_node = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"])
    assert hooks.init.call_count == 1 and hooks.dispose.call_count == 0
    if change == "parameter":
        config["contracts"][0]["parameters"]["operator"] = "or"
    else:
        config["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.replacement"
    await activate(runtime, config)
    assert [call[0] for call in hooks.mock_calls] == ["init", "dispose", "init"]
    disposed = hooks.dispose.call_args.args[0]
    assert disposed.node["fingerprint"] == old_node["fingerprint"]
    assert disposed.node["disposed"]
    node = runtime.state.tables["node_state"]["fixture.echo"]
    assert node["fingerprint"] != old_node["fingerprint"]
    assert node["inits"] == 1 and "disposed" not in node
    assert node["producer_epoch"] == runtime.state.epoch_id
    # Revisions without a semantic change and ordinary evaluations do not re-init.
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    assert hooks.init.call_count == 2 and hooks.dispose.call_count == 1


@pytest.mark.parametrize("pause", ["disable", "remove"])
async def test_producer_lifecycle_removal_and_resume(runtime, config, pause):
    hooks = lifecycle_producer(runtime)
    instance = contract("echo", "boolean", ["source.a"], dict(operator="not"))
    config["contracts"] = [instance]
    await activate(runtime, config)
    if pause == "disable":
        instance["enabled"] = False
    else:
        config["contracts"] = []
    await activate(runtime, config)
    assert [call[0] for call in hooks.mock_calls] == ["init", "dispose"]
    assert "fixture.echo" not in runtime.snapshot()["contracts"]
    node = (await runtime.store.load()).tables["node_state"]["fixture.echo"]
    assert node["disposed"] and "producer_epoch" not in node
    await activate(runtime, config)
    assert hooks.dispose.call_count == 1
    instance["enabled"] = True
    config["contracts"] = [instance]
    await activate(runtime, config)
    assert [call[0] for call in hooks.mock_calls] == ["init", "dispose", "init"]
    assert "fixture.echo" in runtime.snapshot()["contracts"]
    assert (
        runtime.state.tables["node_state"]["fixture.echo"]["producer_epoch"]
        == runtime.state.epoch_id
    )
    await runtime.submit("observation", obs(runtime))
    assert hooks.init.call_count == 2


async def test_producer_lifecycle_ordered_stop_and_restart(runtime, config):
    hooks = lifecycle_producer(runtime)
    config["contracts"] = [contract("echo", "boolean", ["source.a"], dict(operator="not"))]
    await activate(runtime, config)
    runtime.ingest(obs(runtime))
    epoch = runtime.state.epoch_id
    await runtime.stop()
    assert runtime.queue.empty() and runtime.task.done()
    stopped = await runtime.store.load()
    assert stopped.tables["runtime_epoch"][epoch]["ended_at"] is not None
    assert stopped.tables["node_state"]["fixture.echo"]["disposed"]
    assert "producer_epoch" not in stopped.tables["node_state"]["fixture.echo"]
    assert [call[0] for call in hooks.mock_calls] == ["init", "dispose"]
    await runtime.stop()
    assert hooks.dispose.call_count == 1
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        assert restarted.state.epoch_id != epoch
        node = restarted.state.tables["node_state"]["fixture.echo"]
        assert node["inits"] == 2 and node["producer_epoch"] == restarted.state.epoch_id
        assert [call[0] for call in hooks.mock_calls] == ["init", "dispose", "init"]
        await restarted.submit("observation", obs(restarted))
        assert hooks.init.call_count == 2
    finally:
        await restarted.stop()
    assert [call[0] for call in hooks.mock_calls] == ["init", "dispose", "init", "dispose"]


@pytest.mark.parametrize(
    "transform,value,expected",
    [
        (dict(kind="type_cast", target="bool"), "off", False),
        (dict(kind="type_cast", target="number"), "12.5", 12.5),
        (dict(kind="type_cast", target="text"), 7, "7"),
        (dict(kind="unit_scale", factor=2, offset=-1), "4", 7),
    ],
)
def test_generic_transforms(config, transform, value, expected):
    from core_contracts.adapters.ha import transform as apply

    config["bindings"][0]["transform"] = transform
    config["sources"][0]["source_type"] = {"bool": "boolean", "number": "numeric", "text": "text"}[
        transform.get("target", "number")
    ]
    document = validate(config, type_registry())
    assert apply(value, document.bindings[0], document) == expected


async def test_diagnostics_exposes_bridge_and_overflow(runtime, config, aiohttp_client, tmp_path):
    await activate(runtime, config)
    runtime.bridge_version = "test-version"
    runtime.bridge_subscriptions = 2
    runtime.overflow_serial = 3
    tokens = Tokens(tmp_path)
    client = await aiohttp_client(API(runtime, tokens, str(uuid4())).application())
    result = await (
        await client.get(
            "/api/v1/diagnostics", headers={"Authorization": "Bearer " + tokens.values["admin"]}
        )
    ).json()
    assert (
        result["bridge_version"],
        result["bridge_subscriptions"],
        result["ingest_overflow_count"],
    ) == ("test-version", 2, 3)


async def test_versioned_acceptance_registry_and_offline_script():
    from pathlib import Path

    from dev.phase1a_acceptance import registry, simulate

    path = Path(__file__).resolve().parents[1] / "docs/platform-alpha1/phase1a-test-registry.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == registry()
    validate(document, type_registry())
    assert all("cc_alpha_test_" in b["adapter"]["ha_entity_id"] for b in document["bindings"])
    assert (await simulate())["passed"]


async def test_old_adapter_revision_cannot_restore_readiness_or_evidence(runtime, config):
    from core_contracts.runtime import SnapshotBatch

    await activate(runtime, config)
    old = obs(runtime).model_copy(update={"registry_revision": 1})
    config["bindings"][0]["adapter"]["ha_entity_id"] = "input_boolean.cc_alpha_test_repaired"
    await activate(runtime, config)
    before = runtime.snapshot()
    await runtime.submit("snapshot", SnapshotBatch([old], 1))
    await runtime.submit("observation", old)
    runtime.ingest_snapshot([old], 1)
    runtime.ingest(old)
    await runtime.queue.join()
    assert runtime.snapshot() == before
    assert not runtime.ready and not runtime.first_snapshot


async def test_disabled_optional_dependency_degrades_without_blocking_activation(runtime, config):
    config["contracts"] = [
        contract("disabled", "echo", ["source.a"]),
        contract(
            "fusion", "fusion", ["source.a", "fixture.disabled"], dict(strategy="first_healthy")
        ),
    ]
    config["contracts"][0]["enabled"] = False
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    assert field(runtime, "fusion")["value"] is True
    assert field(runtime, "fusion")["quality"] == "degraded"
    inputs = runtime._inputs(runtime.config.contracts[1], runtime.config, runtime.state)
    assert inputs["b"].reasons[0].code == ReasonCode.DEPENDENCY_UNAVAILABLE


def test_command_schema_cannot_ignore_extra_arguments():
    from pydantic import BaseModel

    class OpenArgs(BaseModel):
        confirm: bool

    with pytest.raises(ValueError, match="closed schema"):
        TypeRegistry().register(
            replace(type_registry().get("test.state_machine", 1), commands={"request_b": OpenArgs})
        )


def test_timestamp_field_survives_json_roundtrip_and_normalizes_utc():
    schema = Schema(
        fields={"at": FieldSchema(type="timestamp", required=True, physical_state=False)}
    )
    original = FieldValue(status="valid", quality="healthy", value="2026-01-01T13:00:00+01:00")
    value = validate_fields(schema, {"at": original}, NOW)["at"]
    restored = FieldValue.model_validate_json(value.model_dump_json())
    assert validate_fields(schema, {"at": restored}, NOW)["at"].value == NOW


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_numeric_transform_rejects_nonfinite_json_values(config, raw):
    from core_contracts.adapters.ha import transform

    config["bindings"][0]["transform"] = dict(kind="type_cast", target="number")
    config["sources"][0]["source_type"] = "numeric"
    parsed = validate(config, type_registry())
    with pytest.raises(ValueError, match="non-finite"):
        transform(raw, parsed.bindings[0], parsed)
