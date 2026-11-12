import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import definition, registry
from .test_workbench_api import api_client

ACTOR = {"kind": "token", "role": "admin"}


def manual_config(config, *, ttl=None, future=False):
    data = registry(copy.deepcopy(config), definition())
    data["config_schema_version"] = 2
    data["dependencies"] = [
        {"dependency_id": "dep.manual", "kind": "manual", "monitor": "internal"}
    ]
    data["sources"][0].update(
        origin="manual",
        manual_allow_future=future,
        dependency_id="dep.manual",
        freshness={"mode": "periodic_ttl", "interval_s": ttl, "future_tolerance_s": 0}
        if ttl
        else {"mode": "event_stateful", "future_tolerance_s": 0},
    )
    data["bindings"][0] = dict(
        binding_id="binding.a", source_id="source.a", adapter={"kind": "manual"}
    )
    return data


def body(runtime, **patch):
    return dict(
        request_id=str(uuid4()),
        expected_active_revision=runtime.state.active_revision,
        value=True,
        confirm_unbounded=True,
        **patch,
    )


async def enter(runtime, request=None):
    return await runtime.submit(
        "manual_source_enter",
        {"source_id": "source.a", "request": request or body(runtime), "_actor": ACTOR},
    )


def value(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]


async def test_manual_input_is_typed_repeat_safe_and_retracts_without_deleting_history(
    runtime, config
):
    await activate(runtime, manual_config(config))
    request = body(runtime)
    first = await enter(runtime, request)
    assert first["status"] == "accepted" and value(runtime)["value"] is True
    assert await enter(runtime, request) == first
    assert len(await runtime.store.rows("source_observation_history")) == 1
    conflict = await enter(runtime, {**request, "value": False})
    assert conflict["reason"] == "request_id_conflict" and value(runtime)["value"] is True
    rejected = await enter(runtime, {**request, "request_id": str(uuid4()), "value": "true"})
    assert rejected["reason"] == "manual_value_type"
    observation = runtime.state.tables["source_observation_current"]["binding.a"]
    assert observation["origin"] == "manual" and observation["device_time"] is None
    assert observation["manual"]["actor"] == ACTOR
    result = await enter(runtime, {**body(runtime), "action": "retract", "value": None})
    assert result["status"] == "accepted" and value(runtime)["status"] == "unknown"
    assert len(await runtime.store.rows("source_observation_history")) == 2


async def test_manual_ttl_restore_and_absolute_end_never_renew_input(runtime, config):
    await activate(runtime, manual_config(config, ttl=60))
    await enter(runtime)
    before = copy.deepcopy(runtime.state.tables["source_observation_current"]["binding.a"])
    runtime.clock.advance(30)
    await runtime._restore("process_restart")
    assert value(runtime)["status"] == "valid"
    assert runtime.state.tables["source_observation_current"]["binding.a"] == before
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert value(runtime)["status"] == "unknown"
    await enter(
        runtime,
        {
            **body(runtime),
            "valid_until": (runtime.clock.now_utc() + timedelta(seconds=10)).isoformat(),
        },
    )
    runtime.clock.advance(10)
    await runtime.submit("tick")
    assert value(runtime)["status"] == "unknown"


async def test_manual_future_requires_declaration_and_is_not_current_evidence(runtime, config):
    await activate(runtime, manual_config(config))
    start = runtime.clock.now_utc() + timedelta(seconds=30)
    request = {**body(runtime), "valid_from": start.isoformat()}
    assert (await enter(runtime, request))["reason"] == "manual_future_not_enabled"
    await activate(runtime, manual_config(config, future=True))
    await enter(runtime, {**body(runtime), "valid_from": start.isoformat()})
    assert value(runtime)["status"] == "unknown"
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert value(runtime)["status"] == "valid"


async def test_unbounded_confirmation_revision_type_and_semantic_change(runtime, config):
    await activate(runtime, manual_config(config))
    assert (await enter(runtime, {**body(runtime), "confirm_unbounded": False}))[
        "reason"
    ] == "manual_unbounded_confirmation"
    assert (await enter(runtime, {**body(runtime), "expected_active_revision": 99}))[
        "reason"
    ] == "active_revision_conflict"
    await enter(runtime)
    changed = manual_config(config)
    changed["sources"][0]["physical_source_key"] = "manual.changed"
    await activate(runtime, changed)
    assert value(runtime)["status"] == "unknown"


async def test_manual_commit_failure_never_changes_current_value_or_request_result(runtime, config):
    await activate(runtime, manual_config(config))
    request = body(runtime)
    before = copy.deepcopy(runtime.state.tables["source_observation_current"])
    runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("failure"))
    try:
        with pytest.raises(OSError):
            await enter(runtime, request)
        assert runtime.state.tables["source_observation_current"] == before
        assert await runtime.store.get("manual_request", request["request_id"]) is None
    finally:
        runtime.store.before_commit = None


async def test_manual_api_is_admin_only_and_actor_cannot_be_spoofed(
    runtime, config, aiohttp_client, tmp_path
):
    await activate(runtime, manual_config(config))
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    path = "/api/v1/manual-sources/source.a/observations"
    request = body(runtime)
    assert (await client.post(path, json=request)).status == 401
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    assert (await client.post(path, json=request, headers=consumer)).status == 403
    assert (
        await client.post(path, json={**request, "_actor": ACTOR}, headers=headers)
    ).status == 400
    response = await (await client.post(path, json=request, headers=headers)).json()
    assert response["status"] == "accepted"
    result_path = "/api/v1/manual-sources/source.a/requests/" + request["request_id"]
    assert await (await client.get(result_path, headers=headers)).json() == response
    assert (await client.get(result_path, headers=consumer)).status == 403


@pytest.mark.parametrize(
    "patch", ["ha_binding", "ha_dependency", "heartbeat", "address", "transform"]
)
def test_manual_configuration_cannot_masquerade_as_a_transport_source(runtime, config, patch):
    data = manual_config(config)
    if patch == "ha_binding":
        data["bindings"][0]["adapter"] = {"kind": "ha_state", "ha_entity_id": "sensor.other"}
    elif patch == "ha_dependency":
        data["dependencies"][0]["kind"] = "ha"
    elif patch == "heartbeat":
        data["sources"][0]["freshness"] = {
            "mode": "report_heartbeat",
            "interval_s": 60,
            "future_tolerance_s": 0,
        }
    elif patch == "address":
        data["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.other"
    else:
        data["bindings"][0]["transform"] = {"kind": "type_cast", "target": "bool"}
    with pytest.raises(ValueError):
        validate(data, runtime.types)


async def test_manual_number_uses_declared_unit_bounds_and_disabled_consumer_is_not_writable(
    runtime, config
):
    from .test_declarative import schema

    declaration = definition()
    declaration["inputs"][0]["schema"] = schema("number", unit="°C")
    declaration["schema"]["fields"]["value"] = schema("number", unit="°C")
    data = manual_config(config)
    data["definitions"] = [declaration]
    data["sources"][0].update(
        source_type="numeric",
        value_schema=schema("number", unit="°C"),
        manual_minimum=-20,
        manual_maximum=50,
    )
    await activate(runtime, data)
    assert (await enter(runtime, {**body(runtime), "value": 51}))["reason"] == "manual_value_bounds"
    assert (await enter(runtime, {**body(runtime), "value": 21.5}))["status"] == "accepted"
    assert value(runtime)["value"] == 21.5
    data["contracts"][0]["enabled"] = False
    await activate(runtime, data)
    assert (await enter(runtime, {**body(runtime), "value": 22}))["reason"] == "source_not_in_use"


async def test_unrelated_sources_cannot_receive_manual_evidence(runtime, config):
    await activate(runtime, config)
    assert (await enter(runtime))["reason"] == "manual_source_required"
