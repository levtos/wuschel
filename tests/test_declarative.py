"""Data-only definitions use the same engines, transaction and restore boundary."""

import copy

import pytest
from pydantic import ValidationError

from core_contracts.declarative import Evaluation, check_definition, compile_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.quality import FieldValue
from core_contracts.registry import RegistryConfig, fingerprints
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_core import NOW
from .test_phase1a import field
from .test_phase1a import observation as legacy_observation


def observation(runtime, value, name="a", **kwargs):
    item = legacy_observation(runtime, value, name, **kwargs)
    if runtime.config and runtime.config.fingerprint_version == 2:
        return item.model_copy(
            update={"reference_version": 2, "registry_revision": runtime.state.active_revision}
        )
    return item


def schema(kind="bool", **kwargs):
    return dict(type=kind, required=True, physical_state=False, **kwargs)


def literal(value, kind="bool"):
    return dict(op="literal", value=value, schema=schema(kind))


def definition(rule=None):
    return dict(
        type_id="test.declarative",
        type_version=1,
        inputs=[dict(name="a", schema=schema())],
        schema=dict(fields={"value": schema()}, available_projection=["value"]),
        outputs={"value": rule or dict(op="input", name="a")},
    )


def registry(config, data):
    config["fingerprint_version"] = 2
    config["definitions"] = [data]
    input_schema = data["inputs"][0]["schema"]
    config["sources"][0]["value_schema"] = input_schema.copy()
    config["sources"][0]["source_type"] = {
        "bool": "boolean",
        "number": "numeric",
        "text": "text",
    }.get(input_schema["type"], "state")
    if input_schema["type"] in {"bool", "number", "text"}:
        config["bindings"][0]["transform"] = dict(kind="type_cast", target=input_schema["type"])
    else:
        config["bindings"][0].pop("transform", None)
    config["contracts"][0].update(type_id=data["type_id"], type_version=data["type_version"])
    return config


def value(item):
    return FieldValue(status="valid", value=item, quality="healthy", since_at=NOW)


def test_registry_evaluation_and_graph_budgets(config):
    from core_contracts.validation import RegistryValidationError

    document = registry(config, definition(dict(op="and", args=[literal(True)] * 60)))
    prototype = copy.deepcopy(document["contracts"][0])
    document["contracts"] = [{**prototype, "contract_id": f"fixture.c{i}"} for i in range(70)]
    with pytest.raises(RegistryValidationError) as error:
        validate(document, type_registry())
    assert any(issue.code == "registry_evaluation_limit" for issue in error.value.issues)
    document["definitions"] = [definition()]
    document["contracts"] = [
        {
            **prototype,
            "contract_id": f"fixture.c{i}",
            "inputs": [{"name": "a", "ref": f"fixture.c{i - 1}.value" if i else "source.a"}],
        }
        for i in range(65)
    ]
    with pytest.raises(RegistryValidationError) as error:
        validate(document, type_registry())
    assert any(issue.code == "contract_graph_depth" for issue in error.value.issues)


def test_liveness_fingerprint_propagation_and_cycle_rejection(config):
    from core_contracts.validation import RegistryValidationError

    config["fingerprint_version"] = 2
    monitor = copy.deepcopy(config["sources"][0])
    monitor.update(source_id="source.monitor", physical_source_key="physical.monitor")
    config["sources"].append(monitor)
    config["bindings"].append(
        {
            "binding_id": "binding.monitor",
            "source_id": "source.monitor",
            "adapter": {"kind": "ha_state", "ha_entity_id": "sensor.monitor"},
            "transform": {"kind": "type_cast", "target": "bool"},
        }
    )
    config["sources"][0]["freshness"] = {
        "mode": "liveness_source",
        "liveness_source": "source.monitor",
        "interval_s": 10,
        "future_tolerance_s": 1,
    }
    before = fingerprints(validate(config, type_registry()), type_registry())
    monitor["freshness"]["future_tolerance_s"] = 2
    after = fingerprints(validate(config, type_registry()), type_registry())
    assert before["source.a"] != after["source.a"]
    monitor["freshness"] = {
        "mode": "liveness_source",
        "liveness_source": "source.a",
        "interval_s": 10,
        "future_tolerance_s": 1,
    }
    with pytest.raises(RegistryValidationError):
        validate(config, type_registry())


def test_documented_registry_example_is_valid():
    import json
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "docs/alpha4/example-registry.json"
    document = json.loads(example.read_text(encoding="utf-8"))
    validated = validate(document, type_registry())
    assert validated.definitions and validated.fingerprint_version == 2


def test_trace_identifies_inputs_and_fusion_selection_without_raw_values():
    data = definition(
        dict(
            op="fusion",
            strategy="first_healthy",
            args=[
                dict(op="input", name="a"),
                literal(False),
            ],
        )
    )
    evaluation = Evaluation(DeclarativeDefinition.model_validate(data), {"a": value(True)}, NOW)
    assert evaluation.evaluate(evaluation.definition.outputs["value"]).value is True
    assert any(step.get("name") == "a" for step in evaluation.trace)
    selection = next(step for step in evaluation.trace if step["op"] == "fusion_selection")
    assert selection["selected_indices"] == [0]
    assert all("value" not in step for step in evaluation.trace)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (dict(op="not", args=[dict(op="input", name="a")]), False),
        (dict(op="and", args=[dict(op="input", name="a"), literal(False)]), False),
        (dict(op="or", args=[dict(op="input", name="a"), literal(False)]), True),
        (
            dict(
                op="compare", comparison="gt", left=literal(5, "number"), right=literal(3, "number")
            ),
            True,
        ),
        (dict(op="map", arg=literal("on", "text"), schema=schema(), entries={'"on"': True}), True),
        (
            dict(
                op="first_match",
                cases=[
                    dict(when=literal(False), then=literal(False)),
                    dict(when=literal(True), then=literal(True)),
                ],
            ),
            True,
        ),
        (dict(op="fusion", strategy="any_true", args=[literal(False), literal(True)]), True),
    ],
)
def test_closed_nested_operator_semantics(rule, expected):
    data = DeclarativeDefinition.model_validate(definition(rule))
    check_definition(data)
    assert (
        Evaluation(data, {"a": value(True)}, NOW).evaluate(data.outputs["value"]).value == expected
    )
    assert data.model_dump(mode="json")["schema"]["fields"]["value"]["type"] == "bool"


def test_formula_and_internal_dag():
    data = definition(
        dict(
            op="compare",
            comparison="eq",
            left=dict(op="ref", name="sum"),
            right=literal(5, "number"),
        )
    )
    data["rules"] = {
        "sum": dict(
            op="formula",
            name="test.sum",
            version=1,
            args=[literal(2, "number"), literal(3, "number")],
        )
    }
    parsed = DeclarativeDefinition.model_validate(data)
    check_definition(parsed)
    evaluation = Evaluation(parsed, {}, NOW)
    assert evaluation.evaluate(parsed.outputs["value"]).value is True
    assert evaluation.memo["sum"].value == 5


def test_new_code_registered_formula_discovered_without_editor_special_case(monkeypatch):
    from core_contracts.capabilities import capabilities
    from core_contracts.engines import FORMULA_SCHEMAS, FORMULAS, FormulaSchema
    from core_contracts.schema import FieldSchema

    number = FieldSchema.model_validate(schema("number"))
    monkeypatch.setitem(FORMULAS._items, ("test.product", 1), lambda items: items[0] * items[1])
    monkeypatch.setitem(FORMULA_SCHEMAS, ("test.product", 1), FormulaSchema(number, number))
    data = definition(
        dict(
            op="compare",
            comparison="eq",
            left=dict(
                op="formula",
                name="test.product",
                version=1,
                args=[literal(2, "number"), literal(3, "number")],
            ),
            right=literal(6, "number"),
        )
    )
    parsed = DeclarativeDefinition.model_validate(data)
    check_definition(parsed)
    assert Evaluation(parsed, {}, NOW).evaluate(parsed.outputs["value"]).value is True
    discovered = capabilities(None, type_registry())
    assert "test.product" in {item["name"] for item in discovered["formulas"]}
    assert (
        "test.product"
        in discovered["schemas"]["registry"]["$defs"]["FormulaRule"]["properties"]["name"]["enum"]
    )


def test_typed_command_argument_schemas_all_types():
    from core_contracts.declarative import command_model
    from core_contracts.declarative_models import CommandDefinition

    command = CommandDefinition(
        arguments={
            name: declaration
            for name, declaration in {
                "enabled": schema(),
                "count": schema("number"),
                "name": schema("text"),
                "choice": schema("enum", value_catalog=["a", "b"]),
                "at": schema("timestamp"),
                "metadata": schema("object"),
                "optional": {**schema("text"), "required": False},
            }.items()
        }
    )
    model = command_model("test", command)
    valid = dict(
        enabled=True,
        count=2,
        name="fixture",
        choice="a",
        at="2026-01-01T12:00:00Z",
        metadata={"test": True},
    )
    assert model.model_validate(valid, strict=True).at.endswith("Z")
    assert model.model_json_schema()["properties"]["at"]["format"] == "date-time"
    for name, value in (
        ("enabled", "true"),
        ("count", float("nan")),
        ("choice", "c"),
        ("at", "2026-01-01T12:00:00"),
        ("metadata", "text"),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**valid, name: value}, strict=True)


async def test_remaining_temporal_and_bucket_operations(runtime, config):
    data = definition()
    data["inputs"][0]["schema"] = schema("number")
    data["schema"]["fields"] = {
        "value": schema(),
        "delta": schema("number"),
        "age": schema("number"),
    }
    data["outputs"] = {
        "value": dict(
            op="bucket",
            id="hysteresis",
            initial=False,
            lower=2,
            upper=5,
            arg=dict(op="input", name="a"),
        ),
        "delta": dict(
            op="temporal",
            id="difference",
            operation="delta",
            duration_s=1,
            args=[dict(op="input", name="a"), literal(1, "number")],
        ),
        "age": dict(
            op="temporal",
            id="age",
            operation="age",
            duration_s=1,
            args=[dict(op="input", name="a")],
        ),
    }
    registry(config, data)["sources"][0]["value_schema"] = schema("number")
    config["sources"][0]["source_type"] = "numeric"
    config["bindings"][0]["transform"] = dict(kind="type_cast", target="number")
    await activate(runtime, config)
    await runtime.submit("observation", observation(runtime, 6))
    assert field(runtime)["value"] is True
    assert field(runtime, key="delta")["value"] == 5
    assert field(runtime, key="age")["value"] == 0
    runtime.clock.advance(1)
    await runtime.submit(
        "observation", observation(runtime, 3).model_copy(update={"ingest_seq": 2})
    )
    assert field(runtime)["value"] is True
    assert runtime.latches["fixture.echo:hysteresis"].latch is True
    assert "hysteresis" not in runtime.state.tables["node_state"]["fixture.echo"]["rules"]


@pytest.mark.parametrize("operation", ["dwell", "edge"])
async def test_declarative_dwell_and_edge(runtime, config, operation):
    data = definition(
        dict(
            op="temporal",
            id="timer",
            operation=operation,
            duration_s=2,
            args=[dict(op="input", name="a")],
        )
    )
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, False))
    runtime.clock.advance(1)
    await runtime.submit(
        "observation", observation(runtime, True).model_copy(update={"ingest_seq": 2})
    )
    assert field(runtime)["value"] is (operation == "edge")
    runtime.clock.advance(2)
    await runtime.submit("tick")
    assert field(runtime)["value"] is (operation == "dwell")


def test_definition_errors_are_field_addressable(config):
    from core_contracts.validation import RegistryValidationError

    data = definition(dict(op="input", name="missing"))
    data["rules"] = {"bad": dict(op="not", args=[literal(1, "number")])}
    with pytest.raises(RegistryValidationError) as error:
        validate(registry(config, data), type_registry())
    paths = {item.path for item in error.value.issues}
    assert "/definitions/0/rules/bad" in paths
    assert "/definitions/0/outputs/value" in paths


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda d: d.update(outputs={"value": literal("bad", "text")}), "rule_type_mismatch"),
        (
            lambda d: d.update(outputs={"value": dict(op="input", name="missing")}),
            "unknown_rule_input",
        ),
        (lambda d: d.update(rules={"x": dict(op="ref", name="x")}), "internal_cycle"),
        (lambda d: d.update(outputs={"value": dict(op="ref", name="missing")}), "unknown_internal"),
        (
            lambda d: d.update(
                outputs={
                    "value": dict(op="formula", name="exec", version=1, args=[literal(1, "number")])
                }
            ),
            "unknown_formula",
        ),
        (
            lambda d: d.update(rules={str("n" + str(i)): literal(True) for i in range(256)}),
            "definition_resource_limit",
        ),
    ],
)
def test_type_and_resource_rejection(change, error):
    data = definition()
    change(data)
    with pytest.raises(ValueError, match=error):
        compile_definition(DeclarativeDefinition.model_validate(data))


@pytest.mark.parametrize(
    "bad",
    [
        dict(op="python", code="return True"),
        dict(op="eval", expression="1"),
        dict(op="input", name="a", code="evil"),
        dict(op="not", args=[]),
        dict(op="literal", schema=schema(), value="true"),
    ],
)
def test_unregistered_execution_and_invalid_data_rejected(bad):
    with pytest.raises(ValidationError):
        DeclarativeDefinition.model_validate(definition(bad))


def test_duplicate_stateful_id_and_expansion_depth():
    temporal = dict(
        op="temporal",
        id="stable",
        operation="stable_for",
        duration_s=2,
        args=[dict(op="input", name="a")],
    )
    with pytest.raises(ValueError, match="duplicate_stateful_id"):
        check_definition(
            DeclarativeDefinition.model_validate(
                definition(dict(op="and", args=[temporal, temporal]))
            )
        )
    data = definition(dict(op="ref", name="n0"))
    data["rules"] = {f"n{i}": dict(op="ref", name=f"n{i + 1}") for i in range(17)}
    data["rules"]["n17"] = literal(True)
    with pytest.raises(ValueError, match="definition_resource_limit"):
        check_definition(DeclarativeDefinition.model_validate(data))


def test_registry_types_isolated_and_legacy_fingerprints(config):
    types = type_registry()
    before = fingerprints(validate(config, types), types)
    with_defaults = RegistryConfig.model_validate(config).model_dump(mode="json")
    assert fingerprints(validate(with_defaults, types), types) == before
    validate(registry(config, definition()), types)
    assert ("test.declarative", 1) not in types.types
    config["sources"][0]["value_schema"] = schema("number")
    from core_contracts.validation import RegistryValidationError

    with pytest.raises(RegistryValidationError) as error:
        validate(config, types)
    assert "input_type_mismatch" in {issue.code for issue in error.value.issues}


def test_released_alpha3_acceptance_registry_fingerprint_vectors():
    import json
    from pathlib import Path

    # Generated by the released source tree at 8adb44bf87d77c40bf518e0b8dbf7e1fd0d08ec6,
    # not by this compiler. Every source and contract in its acceptance registry
    # must retain its exact restore identity under the default version-1 mode.
    path = Path(__file__).resolve().parents[1] / "docs/platform-alpha1/phase1a-test-registry.json"
    types = type_registry()
    result = fingerprints(validate(json.loads(path.read_text(encoding="utf-8")), types), types)
    assert result == {
        "source.cc_alpha_test_a": "6d256d685b7dbdb66d4d291fa3de2c6849cfc655612b2e273fc73d2b526af665",
        "source.cc_alpha_test_b": "b5393b3b53ad7b43fe628ea37e592dca43b9dac0a9ab1964b0f51f14d4fa89f5",
        "source.cc_alpha_test_number": "2d7cd4bd9f5fb315372828b877458b91768878b520b9c84e88127ffc91ecd285",
        "test.alpha1.echo": "5c805c090709488132a1a9f887f48791776b11b1c74e725bfbd50eb5557946e3",
        "test.alpha1.fusion": "9ccd79ee64dd8b7c00c32f1f988a30a596271fa295af24eb136a75617af291dd",
        "test.alpha1.grace": "850e5ab30b5be1d3bb60648965c54ae8b4926bbedba5f0638d019950554afbda",
        "test.alpha1.machine": "b70780f093649c5b8259e36398d02f5252afbba3dc1c2bb4d33093a96623a3c6",
        "test.alpha1.multi": "eadfa0f86840c71d0cdfe8013f9bf969f2ceeb2e660f3b77c7d8a3fa545cde93",
        "test.alpha1.not": "b2f4e68d7e880a87a2c3be22db3b51f417590de0381a93e7e7639d9c0b10c465",
        "test.alpha1.stable": "44a9a91e05818180fc9a9765a237763f8ef2420e03f3f55471b9b9aab4c71077",
    }


async def test_data_only_multifield_and_safe_trace(runtime, config):
    data = definition(dict(op="not", args=[dict(op="input", name="a")]))
    data["schema"]["fields"]["label"] = schema("enum", value_catalog=["on", "off"])
    data["outputs"]["label"] = dict(
        op="map",
        arg=dict(op="input", name="a"),
        schema=schema("enum", value_catalog=["on", "off"]),
        entries={"true": "on", "false": "off"},
    )
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    assert field(runtime)["value"] is False
    assert field(runtime, key="label")["value"] == "on"
    trace = runtime.state.tables["node_state"]["fixture.echo"]["trace"]
    assert trace["cost"] <= 512
    assert all("value" not in step for step in trace["steps"])
    await runtime.submit("observation", observation(runtime, "password-value"))
    assert field(runtime)["status"] == "unknown"
    assert "password-value" not in str(runtime.state.tables["node_state"]["fixture.echo"]["trace"])


async def test_temporal_internal_restore_and_deadline(runtime, config):
    data = definition(dict(op="ref", name="held"))
    data["rules"] = {
        "held": dict(
            op="temporal",
            id="grace",
            operation="grace",
            duration_s=10,
            args=[dict(op="input", name="a")],
        )
    }
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    runtime.clock.advance(1)
    await runtime.submit("observation", observation(runtime, None, availability="unavailable"))
    assert field(runtime)["status"] == "held"
    end = field(runtime)["held_until"]
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        assert field(restarted)["held_until"] == end
        restarted.clock.advance(11)
        await restarted.submit("tick")
        assert field(restarted)["status"] == "unknown"
    finally:
        await restarted.stop()


async def test_two_internal_temporal_lifecycles(runtime, config):
    data = definition(
        dict(op="and", args=[dict(op="ref", name="fast"), dict(op="ref", name="slow")])
    )
    data["rules"] = {
        name: dict(
            op="temporal",
            id=name,
            operation="stable_for",
            duration_s=duration,
            args=[dict(op="input", name="a")],
        )
        for name, duration in (("fast", 2), ("slow", 5))
    }
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    runtime.clock.advance(2)
    await runtime.submit("tick")
    assert field(runtime)["value"] is False
    runtime.clock.advance(3)
    await runtime.submit("tick")
    assert field(runtime)["value"] is True
    assert set(runtime.state.tables["node_state"]["fixture.echo"]["rules"]) == {"fast", "slow"}


def machine_definition():
    data = definition(dict(op="machine"))
    data["schema"]["fields"]["value"] = schema("enum", value_catalog=["idle", "active", "done"])
    data["machine"] = dict(
        id="lifecycle",
        states=["idle", "active", "done"],
        initial="idle",
        deadline_s=10,
        sessions=True,
        reinitialize_when=dict(op="input", name="a"),
        commands={"start": dict(arguments={"confirm": schema()})},
        transitions=[
            dict(
                source="idle",
                target="active",
                event="start",
                guard=dict(op="input", name="a"),
                argument_equals={"confirm": True},
                reason="requested",
            ),
            dict(
                source="active",
                target="done",
                event="deadline",
                guard=literal(True),
                reason="elapsed",
                ends_episode=True,
            ),
        ],
    )
    return data


async def test_declarative_machine_commands_restore(runtime, config):
    from datetime import timedelta
    from uuid import uuid4

    data = machine_definition()
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    command = dict(
        command_id=str(uuid4()),
        contract_id="fixture.echo",
        command="start",
        args={"confirm": True},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
    )
    result = await runtime.submit("command", command)
    assert result["status"] == "accepted"
    assert field(runtime)["value"] == "active"
    seq = runtime.state.publication_seq
    assert await runtime.submit("command", command) == result
    assert runtime.state.publication_seq == seq
    state = copy.deepcopy(runtime.state.tables["sm_instance"]["fixture.echo"])
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        assert (
            restarted.state.tables["sm_instance"]["fixture.echo"]["deadline"] == state["deadline"]
        )
        restarted.clock.advance(11)
        await restarted.submit("tick")
        assert field(restarted)["value"] == "done"
    finally:
        await restarted.stop()


async def test_declarative_command_failure_rolls_back_nested_state_and_replays(
    runtime, config, monkeypatch
):
    from datetime import timedelta

    from core_contracts.engines import FORMULAS

    data = machine_definition()
    data["schema"]["fields"]["number"] = schema("number")
    data["outputs"]["number"] = dict(
        op="formula", name="test.sum", version=1, args=[literal(2, "number"), literal(3, "number")]
    )
    await activate(runtime, registry(config, data))
    await runtime.submit("observation", observation(runtime, True))
    original = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"])

    def fail(_):
        raise ArithmeticError("injected engine failure")

    with monkeypatch.context() as patch:
        patch.setitem(FORMULAS._items, ("test.sum", 1), fail)
        command = dict(
            command_id="failed-declarative",
            contract_id="fixture.echo",
            command="start",
            args={"confirm": True},
            origin=dict(kind="test", actor="test", client_name="test"),
            issued_at=runtime.clock.now_utc(),
            valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
        )
        result = await runtime.submit("command", command)
    assert result["status"] == "rejected" and result["reason"] == "evaluation_error"
    assert runtime.state.tables["node_state"]["fixture.echo"] == original
    assert runtime.state.tables["sm_instance"]["fixture.echo"]["state"] == "idle"
    sequence = runtime.state.publication_seq
    assert await runtime.submit("command", command) == result
    assert runtime.state.publication_seq == sequence
    assert (await runtime.submit("command", {**command, "command_id": "successful-declarative"}))[
        "status"
    ] == "accepted"
