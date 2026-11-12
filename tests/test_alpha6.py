"""Issue #17 regressions, using isolated synthetic instances only."""

import copy

import pytest

from core_contracts.validation import RegistryValidationError
from core_contracts.validation import validate_document as validate

from .conftest import activate


async def test_ha_status_empty_active_rollback_and_reactivation(runtime, config):
    assert runtime.service_state()["ha"] == "not_needed"
    empty = {**config, "sources": [], "bindings": [], "contracts": [], "dependencies": []}
    await activate(runtime, empty)
    empty_revision = runtime.state.active_revision
    await activate(runtime, config)
    assert runtime.service_state()["ha"] == "disconnected"
    await runtime.submit("bridge_state", ("connected", "connected"))
    assert runtime.service_state()["ha"] == "connected"
    await runtime.submit(
        "rollback",
        {"revision": empty_revision, "expected_active_revision": runtime.state.active_revision},
    )
    assert runtime.service_state()["ha"] == runtime.service_state()["bridge"] == "not_needed"
    assert runtime.bridge_subscriptions == 0
    # A delayed old transport result cannot claim an unneeded connection.
    await runtime.submit("bridge_state", ("connected", "connected"))
    assert runtime.service_state()["ha"] == "not_needed"
    await activate(runtime, copy.deepcopy(config))
    assert runtime.service_state()["ha"] == "disconnected"


async def test_unused_ha_binding_needs_no_connection(runtime, config):
    config["contracts"][0]["enabled"] = False
    await activate(runtime, config)
    assert runtime.ready
    assert runtime.service_state()["ha"] == "not_needed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_type", "text"),
        (
            "value_schema",
            {"type": "bool", "unit": "changed", "required": True, "physical_state": False},
        ),
    ],
)
async def test_source_lock_survives_removal_and_restart(runtime, config, field, value):
    await activate(runtime, config)
    await activate(runtime, {**config, "sources": [], "bindings": [], "contracts": []})
    await runtime.submit("recover")
    changed = copy.deepcopy(config)
    changed["sources"][0][field] = value
    if field == "source_type":
        changed["bindings"][0]["transform"]["target"] = "text"
    for operation in ("validate", "activate"):
        with pytest.raises(RegistryValidationError) as error:
            if operation == "validate":
                await runtime.submit(operation, changed)
            else:
                await activate(runtime, changed)
        assert error.value.issues[0].code == "immutable_source_type"
    assert runtime.state.active_revision == 2


async def test_source_catalog_only_grows(runtime, config):
    config["bindings"][0].pop("transform")
    config["sources"][0].update(
        source_type="state",
        value_schema={
            "type": "enum",
            "required": True,
            "physical_state": False,
            "value_catalog": ["a"],
        },
    )
    await activate(runtime, config)
    config["sources"][0]["value_schema"]["value_catalog"].append("b")
    await activate(runtime, config)
    config["sources"][0]["value_schema"]["value_catalog"] = ["a"]
    with pytest.raises(RegistryValidationError) as error:
        await activate(runtime, config)
    assert error.value.issues[0].code == "immutable_source_catalog"


def test_transform_type_must_agree_with_source(config):
    config["bindings"][0]["transform"] = {"kind": "type_cast", "target": "number"}
    from core_contracts.testing.contract_types import type_registry

    with pytest.raises(RegistryValidationError) as error:
        validate(config, type_registry())
    assert "binding_type_mismatch" in {i.code for i in error.value.issues}


def test_missing_cast_is_rejected_even_without_a_declarative_consumer(config):
    from core_contracts.testing.contract_types import type_registry

    config["bindings"][0].pop("transform")
    config["contracts"] = []
    with pytest.raises(RegistryValidationError) as error:
        validate(config, type_registry())
    assert error.value.issues[0].code == "binding_cast_required"


@pytest.mark.parametrize(
    "raw,expected",
    [("on", True), ("off", False), ("true", True), ("false", False), (True, True), (False, False)],
)
def test_explicit_bool_cast(config, raw, expected):
    from core_contracts.adapters.ha import transform
    from core_contracts.registry import RegistryConfig

    config["bindings"][0]["transform"] = {"kind": "type_cast", "target": "bool"}
    registry = RegistryConfig.model_validate(config)
    assert transform(raw, registry.bindings[0], registry) is expected


@pytest.mark.parametrize("raw", [0, 1, "yes", "unknown", None])
def test_bool_cast_rejects_other_values(config, raw):
    from core_contracts.adapters.ha import transform
    from core_contracts.registry import RegistryConfig

    config["bindings"][0]["transform"] = {"kind": "type_cast", "target": "bool"}
    registry = RegistryConfig.model_validate(config)
    with pytest.raises(ValueError):
        transform(raw, registry.bindings[0], registry)


async def test_machine_recovers_only_from_valid_evidence_and_reports_state_unknown(runtime, config):
    from datetime import timedelta

    from .test_declarative import machine_definition, observation, registry

    data = machine_definition()
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    episode = runtime.state.tables["sm_instance"]["fixture.echo"]["episode_id"]
    # Repairing a binding changes the fingerprint and discards its old evidence.
    config["bindings"][0]["adapter"]["ha_entity_id"] = "binary_sensor.synthetic_repaired"
    await activate(runtime, config)
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"] == "unknown"
    assert "fixture.echo" not in runtime.state.tables["sm_instance"]
    command = dict(
        command_id="unknown.1",
        contract_id="fixture.echo",
        command="start",
        args={"confirm": True},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
    )
    result = await runtime.submit("command", command)
    assert result["reason"] == "state_unknown"
    assert await runtime.submit("command", command) == result
    for value, extra in [
        (True, {"availability": "unknown"}),
        (False, {}),
        (True, {"observation_kind": "restore"}),
    ]:
        runtime.clock.advance(1)
        item = observation(runtime, value, availability=extra.get("availability", "available"))
        if "observation_kind" in extra:
            item = item.model_copy(update={"observation_kind": extra["observation_kind"]})
        await runtime.submit("observation", item)
        assert (
            runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
            == "unknown"
        )
    runtime.clock.advance(1)
    await runtime.submit("observation", observation(runtime, True))
    restored = runtime.state.tables["sm_instance"]["fixture.echo"]
    assert restored["state"] == "idle" and restored["episode_id"] != episode
    assert restored["transition_reason"] == "reinitialize_when" and restored["evidence"]
    assert restored["origin"] == "automatic"


async def test_constant_recovery_does_not_invent_evidence(runtime, config):
    from .test_declarative import literal, machine_definition, registry

    data = machine_definition()
    data["machine"]["reinitialize_when"] = literal(False)
    report = await runtime.submit("validate", registry(config, data))
    assert report["valid"] and report["warnings"][0]["code"] == "machine_never_recovers"
    await activate(runtime, config)
    # A new version with constant true still has no sensor evidence for a restart.
    data["type_version"] = 2
    data["machine"]["reinitialize_when"] = literal(True)
    await activate(runtime, registry(config, data))
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"] == "unknown"


@pytest.mark.parametrize("identity", ["a" * 129, "ende.", "küche.wärme"])
def test_invalid_identifiers_are_rejected_on_write(config, identity):
    from core_contracts.testing.contract_types import type_registry

    config["contracts"][0]["contract_id"] = identity
    with pytest.raises(RegistryValidationError) as error:
        validate(config, type_registry())
    assert any(i.path == "/contracts/0/contract_id" for i in error.value.issues)


async def test_request_failures_are_safe_and_not_error_logs(
    runtime, config, aiohttp_client, tmp_path, caplog
):
    import logging

    from .test_workbench_api import api_client

    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    caplog.set_level(logging.INFO)
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={"config": {"password": "synthetic-placeholder"}},
    )
    assert response.status == 400
    assert (await response.json())["error"] == "sensitive_document"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "synthetic-placeholder" not in caplog.text
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    for index in range(100):
        await runtime.submit("draft_create", {"draft_id": f"limit_{index}", "config": {}})
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={"config": {}, "expected_active_revision": 0},
    )
    assert (await response.json())["error"] == "draft_limit"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    runtime.store.available = False
    with pytest.raises(RuntimeError):
        await runtime.submit("validate", config)
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    runtime.store.available = True
