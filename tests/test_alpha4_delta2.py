"""Only the two Opus D2 blockers: bounded reachability and atomic guard failure."""

import asyncio
import copy
from datetime import timedelta
from time import perf_counter

import pytest

import core_contracts.runtime as runtime_module
from core_contracts.declarative import Evaluation, all_rules, check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.engines import FORMULAS
from core_contracts.registry import _compile_definition, validate
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError

from .conftest import activate
from .test_core import NOW
from .test_declarative import definition, literal, machine_definition, observation, registry, schema
from .test_workbench_api import api_client


def fanout_definition(*, levels=7, fanout=35, reachable=True):
    data = definition(dict(op="ref", name=f"level{levels}"))
    data["rules"] = {"level0": literal(False)}
    for level in range(1, levels + 1):
        data["rules"][f"level{level}"] = dict(
            op="and", args=[dict(op="ref", name=f"level{level - 1}") for _ in range(fanout)]
        )
    data["machine"] = dict(
        id="lifecycle",
        states=["idle"],
        initial="idle",
        transitions=[],
        reinitialize_when=literal(False),
    )
    if reachable:
        # Search the entire false DAG before finding the directly reachable machine.
        data["schema"]["fields"]["machine"] = schema("enum", value_catalog=["idle"])
        data["outputs"]["machine"] = dict(op="machine")
    return data


@pytest.mark.parametrize("reachable", [True, False])
def test_fanout_reachability_is_bounded_and_definition_local(config, reachable, record_property):
    data = fanout_definition(reachable=reachable)
    assert len(all_rules(DeclarativeDefinition.model_validate(data))) in {255, 256}
    document = registry(config, data)
    _compile_definition.cache_clear()  # Measure cold validation, not the existing type cache.
    started = perf_counter()
    if reachable:
        validate(document, type_registry())
    else:
        with pytest.raises(RegistryValidationError) as error:
            validate(document, type_registry())
        assert any(
            issue.code == "definition.unreachable_machine"
            and issue.path == "/definitions/0/machine"
            for issue in error.value.issues
        )
    elapsed = perf_counter() - started
    record_property("cold_validation_ms", elapsed * 1000)
    assert elapsed < 0.1
    # Reusing names in another definition must not reuse reachability answers.
    other = DeclarativeDefinition.model_validate(fanout_definition(reachable=not reachable))
    if reachable:
        with pytest.raises(ValueError, match="unreachable_machine"):
            check_definition(other)
    else:
        check_definition(other)


@pytest.mark.parametrize("indirect", [False, True])
def test_machine_direct_and_indirect_reachability(indirect):
    data = machine_definition()
    if indirect:
        data["rules"] = {"machine": dict(op="machine"), "alias": dict(op="ref", name="machine")}
        data["outputs"]["value"] = dict(op="ref", name="alias")
    check_definition(DeclarativeDefinition.model_validate(data))


@pytest.mark.parametrize(
    ("levels", "fanout", "cycle", "message"),
    [
        (8, 2, False, "definition_resource_limit"),
        (7, 36, False, "definition_resource_limit"),
        (2, 2, True, "internal_cycle"),
    ],
)
def test_fanout_preserves_depth_node_and_cycle_limits(levels, fanout, cycle, message):
    data = fanout_definition(levels=levels, fanout=fanout)
    if cycle:
        data["rules"]["level0"] = dict(op="ref", name=f"level{levels}")
    with pytest.raises(ValueError, match=message):
        check_definition(DeclarativeDefinition.model_validate(data))


def test_evaluation_cost_limit_remains_effective():
    parsed = DeclarativeDefinition.model_validate(definition(literal(True)))
    evaluation = Evaluation(parsed, {}, NOW)
    for _ in range(512):
        evaluation.evaluate(parsed.outputs["value"])
    with pytest.raises(ValueError, match="evaluation_resource_limit"):
        evaluation.evaluate(parsed.outputs["value"])


async def test_health_http_request_concurrent_with_cold_validation(
    runtime, config, aiohttp_client, tmp_path, monkeypatch, record_property
):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    reader, writer = await asyncio.open_connection(client.host, client.port)
    started = None
    original = runtime_module.validate

    def validate_with_concurrent_request(document, types):
        nonlocal started
        # An independent, already connected HTTP socket sends while the POST's
        # synchronous validator runs. Preservation may validate again; measure from
        # the FIRST pass and send one request on this Connection: close socket.
        if started is None:
            started = perf_counter()
            writer.write(
                b"GET /health/live HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
        return original(document, types)

    async def health_response():
        response = await reader.read()
        assert started is not None
        elapsed = perf_counter() - started
        record_property("concurrent_health_ms", elapsed * 1000)
        assert response.startswith(b"HTTP/1.1 200")
        assert elapsed < 0.2

    monkeypatch.setattr(runtime_module, "validate", validate_with_concurrent_request)
    _compile_definition.cache_clear()
    try:
        response, _ = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    "/api/v1/registry/validate",
                    headers=headers,
                    json=registry(config, fanout_definition()),
                ),
                health_response(),
            ),
            timeout=2,
        )
        assert response.status == 200
        assert (await response.json())["valid"] is True
    finally:
        writer.close()
        await writer.wait_closed()


async def prepare_guard_case(runtime, config):
    data = machine_definition()
    data["machine"]["transitions"][0]["guard"] = dict(
        op="compare",
        comparison="eq",
        left=dict(
            op="formula",
            name="test.sum",
            version=1,
            args=[literal(2, "number"), literal(3, "number")],
        ),
        right=literal(5, "number"),
    )
    data["schema"]["fields"].update(latch=schema(), waiting=schema())
    data["outputs"].update(
        latch=dict(
            op="bucket", id="latch", initial=False, lower=2, upper=5, arg=literal(6, "number")
        ),
        waiting=dict(
            op="temporal",
            id="waiting",
            operation="stable_for",
            duration_s=30,
            args=[dict(op="input", name="a")],
        ),
    )
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    assert runtime.latches["fixture.echo:latch"].latch is True
    assert runtime.state.tables["node_state"]["fixture.echo"]["rules"]["waiting"]
    assert runtime.state.tables["sm_instance"]["fixture.echo"]["deadline"]
    now = runtime.clock.now_utc()
    return dict(
        command_id="guard-error",
        contract_id="fixture.echo",
        command="start",
        args={"confirm": True},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=now.isoformat(),
        valid_until=(now + timedelta(seconds=30)).isoformat(),
    )


def effects(runtime):
    return copy.deepcopy(
        (
            runtime.state.publication_seq,
            {name: rows for name, rows in runtime.state.tables.items() if name != "command_log"},
            runtime.latches,
            runtime.pending_latches,
            runtime.pending_refresh,
        )
    )


async def assert_replay(runtime, client, headers, command, expected):
    before, generation = effects(runtime), runtime.state.generation
    response = await client.post("/api/v1/commands", headers=headers, json=command)
    assert response.status == 200
    assert await response.json() == expected
    assert effects(runtime) == before
    assert runtime.state.generation == generation  # No commit or publication on pure replay.


async def exercise_guard_failure(runtime, config, aiohttp_client, tmp_path, monkeypatch):
    command = await prepare_guard_case(runtime, config)
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    before = effects(runtime)
    persisted_before = (await runtime.store.load()).tables
    queue = runtime.subscribe()

    def fail(_):
        raise ZeroDivisionError("synthetic guard formula failure")

    try:
        with monkeypatch.context() as patch:
            patch.setitem(FORMULAS._items, ("test.sum", 1), fail)
            response = await client.post("/api/v1/commands", headers=headers, json=command)
        expected = dict(
            command_id=command["command_id"], status="rejected", reason="evaluation_error"
        )
        assert response.status == 200
        assert await response.json() == expected
        assert effects(runtime) == before
        assert queue.empty()
        stored = await runtime.store.get("command_log", command["command_id"])
        assert stored["result"] == expected
        persisted_after = (await runtime.store.load()).tables
        assert {k: v for k, v in persisted_after.items() if k != "command_log"} == {
            k: v for k, v in persisted_before.items() if k != "command_log"
        }
        await assert_replay(runtime, client, headers, command, expected)
        assert queue.empty()
        # The formula now works; only a new ID may execute the command.
        response = await client.post(
            "/api/v1/commands",
            headers=headers,
            json={**command, "command_id": "valid-guard"},
        )
        assert response.status == 200
        assert (await response.json())["status"] == "accepted"
        assert runtime.state.tables["sm_instance"]["fixture.echo"]["state"] == "active"
        assert runtime.state.publication_seq > before[0]
        return command, expected
    finally:
        runtime.subscribers.discard(queue)


async def test_guard_error_http_persistence_and_restart(
    runtime, config, aiohttp_client, tmp_path, monkeypatch
):
    command, expected = await exercise_guard_failure(
        runtime, config, aiohttp_client, tmp_path, monkeypatch
    )
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        _, client, headers = await api_client(restarted, aiohttp_client, tmp_path)
        await assert_replay(restarted, client, headers, command, expected)
        assert (await restarted.store.get("command_log", command["command_id"]))[
            "result"
        ] == expected
    finally:
        await restarted.stop()


@pytest.mark.parametrize("failure_at", ["guard_infrastructure", "commit", "lookup"])
async def test_guard_rejection_does_not_mask_infrastructure_failure(
    runtime, config, aiohttp_client, tmp_path, monkeypatch, failure_at
):
    command = await prepare_guard_case(runtime, config)
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    before = effects(runtime)

    def fail_formula(_):
        if failure_at == "guard_infrastructure":
            raise OSError("synthetic infrastructure failure")
        raise ZeroDivisionError("synthetic guard formula failure")

    def fail_commit():
        raise OSError("synthetic commit failure")

    async def fail_lookup(*args):
        raise OSError("synthetic lookup failure")

    with monkeypatch.context() as patch:
        patch.setitem(FORMULAS._items, ("test.sum", 1), fail_formula)
        if failure_at == "commit":
            patch.setattr(runtime.store, "before_commit", fail_commit)
        if failure_at == "lookup":
            patch.setattr(runtime.store, "get", fail_lookup)
        response = await client.post("/api/v1/commands", headers=headers, json=command)
    assert response.status == 503
    assert await response.json() == {"error": "persistence_unavailable"}
    assert await runtime.store.get("command_log", command["command_id"]) is None
    assert effects(runtime) == before
