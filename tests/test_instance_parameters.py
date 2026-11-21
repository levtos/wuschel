import copy

import pytest
from pydantic import ValidationError

from core_contracts.declarative import Evaluation, compile_machine
from core_contracts.declarative_models import ParameterDefinition
from core_contracts.instance_parameters import parameter_model
from core_contracts.registry import fingerprints
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import NOW, definition, machine_definition, registry, schema, value


def parameterized(config):
    data = definition({"op": "parameter", "name": "near"})
    data.update(
        language_version=2,
        parameter_schema={
            name: {"schema": schema("number", unit="m"), "minimum": 0, "label": name}
            for name in ("near", "far")
        },
        parameter_relations=[{"left": "near", "comparison": "lt", "right": "far"}],
    )
    data["schema"]["fields"]["value"] = schema("number", unit="m")
    doc = registry(config, data)
    doc["contracts"][0]["parameters"] = {"near": 10, "far": 100}
    return doc


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"near": "10", "far": 100},
        {"near": True, "far": 100},
        {"near": -1, "far": 100},
        {"near": 100, "far": 10},
        {"near": 10, "far": 100, "extra": 2},
    ],
)
def test_parameter_errors_block_activation_with_pointer(config, parameters):
    doc = parameterized(config)
    doc["contracts"][0]["parameters"] = parameters
    with pytest.raises(RegistryValidationError) as error:
        validate(doc, type_registry())
    assert any(issue.path.startswith("/contracts/0/parameters") for issue in error.value.issues)


def test_parameter_relation_marks_both_fields_without_reflecting_input_values(config):
    doc = parameterized(config)
    doc["contracts"][0]["parameters"] = {"near": 1000, "far": 150}

    with pytest.raises(RegistryValidationError) as error:
        validate(doc, type_registry())

    issues = [
        issue for issue in error.value.issues if issue.code == "parameter.parameter_relation_failed"
    ]
    assert {issue.path for issue in issues} == {
        "/contracts/0/parameters/near",
        "/contracts/0/parameters/far",
    }
    assert all(
        issue.parameters == {"left": "near", "right": "far", "comparison": "lt"} for issue in issues
    )


async def test_instances_do_not_share_parameters_and_revision_resets_baseline(runtime, config):
    doc = parameterized(config)
    second = copy.deepcopy(doc["contracts"][0])
    second.update(contract_id="example.second", parameters={"near": 20, "far": 100})
    doc["contracts"].append(second)
    await activate(runtime, doc)
    first_id = doc["contracts"][0]["contract_id"]
    before = runtime.state.tables["node_state"][first_id]["fingerprint"]
    assert runtime.snapshot()["contracts"][first_id]["fields"]["value"]["value"] == 10
    assert runtime.snapshot()["contracts"]["example.second"]["fields"]["value"]["value"] == 20
    doc["contracts"][0]["parameters"]["near"] = 30
    await activate(runtime, doc)
    assert runtime.state.tables["node_state"][first_id]["fingerprint"] != before
    assert doc["definitions"][0]["type_version"] == 1
    assert runtime.snapshot()["contracts"][first_id]["fields"]["value"]["value"] == 30
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        assert restored.snapshot()["contracts"][first_id]["fields"]["value"]["value"] == 30
    finally:
        await restored.stop()


def test_parameter_values_affect_fingerprint_display_name_does_not(config):
    doc = parameterized(config)
    first = fingerprints(validate(doc, type_registry()), type_registry())
    doc["contracts"][0]["display_name"] = "Anderer Anzeigename"
    assert fingerprints(validate(doc, type_registry()), type_registry()) == first
    doc["contracts"][0]["parameters"]["near"] = 20
    assert fingerprints(validate(doc, type_registry()), type_registry()) != first


@pytest.mark.parametrize(
    "comparison,near,far",
    [
        ("lt", "2026-01-01T12:00:00+02:00", "2026-01-01T11:00:00Z"),
        ("eq", "2026-01-01T12:00:00+02:00", "2026-01-01T10:00:00Z"),
    ],
)
def test_timestamp_relations_compare_instants_instead_of_offset_strings(
    config, comparison, near, far
):
    doc = parameterized(config)
    definition = doc["definitions"][0]
    definition["parameter_schema"] = {
        name: {"schema": schema("timestamp")} for name in ("near", "far")
    }
    definition["parameter_relations"][0]["comparison"] = comparison
    definition["schema"]["fields"]["value"] = schema("timestamp")
    doc["contracts"][0]["parameters"] = {"near": near, "far": far}
    validate(doc, type_registry())


@pytest.mark.parametrize("standard", [None, False, "420", -1, 1440])
def test_declared_default_must_fit_the_parameter_type_and_bounds(standard):
    with pytest.raises(ValidationError, match="parameter_default"):
        ParameterDefinition.model_validate(
            {"schema": schema("number"), "minimum": 0, "maximum": 1439, "default": standard}
        )


def test_parameter_metadata_is_optional_and_preserves_old_export():
    row = ParameterDefinition.model_validate({"schema": schema("number")})
    assert not {"default", "group", "display"} & row.model_dump().keys()
    described = ParameterDefinition.model_validate(
        {
            "schema": schema("number", unit="minute_of_day"),
            "default": 420,
            "minimum": 0,
            "maximum": 1439,
            "group": "Synthetic schedule",
            "display": "time_of_day",
        }
    )
    exported = described.model_dump(mode="json")
    assert exported["default"] == 420
    assert exported["group"] == "Synthetic schedule"
    assert exported["display"] == "time_of_day"


def test_map_resolves_the_selected_parameter_with_default_and_override(config):
    data = definition(
        {
            "op": "map",
            "arg": {"op": "input", "name": "a"},
            "schema": schema(),
            "entries": {"true": {"op": "parameter", "name": "enabled"}, "false": True},
        }
    )
    data.update(
        language_version=2,
        parameter_schema={
            "enabled": {
                "schema": {**schema(), "required": False},
                "default": False,
                "display": "weekday",
            }
        },
    )
    checked = validate(registry(config, data), type_registry())
    declaration = checked.definitions[0]
    assert parameter_model(declaration).model_validate({}).model_dump() == {"enabled": False}
    output = declaration.outputs["value"]
    week_day = value(True).model_copy(update={"evidence": ("source.synthetic",)})
    assert Evaluation(declaration, {"a": week_day}, NOW).evaluate(output).value is False
    changed = Evaluation(declaration, {"a": week_day}, NOW, parameters={"enabled": True})
    assert changed.evaluate(output).value is True
    assert changed.evaluate(output).evidence == week_day.evidence
    assert Evaluation(declaration, {"a": value(False)}, NOW).evaluate(output).value is True
    assert Evaluation(declaration, {}, NOW).evaluate(output).status == "unknown"


def test_map_parameter_type_is_checked_at_the_entry(config):
    data = definition(
        {
            "op": "map",
            "arg": {"op": "input", "name": "a"},
            "schema": schema(),
            "entries": {"true": {"op": "parameter", "name": "number"}},
        }
    )
    data.update(language_version=2, parameter_schema={"number": {"schema": schema("number")}})
    doc = registry(config, data)
    doc["contracts"][0]["parameters"] = {"number": 2}
    with pytest.raises(RegistryValidationError) as error:
        validate(doc, type_registry())
    assert any(
        issue.code == "definition.map_type_mismatch"
        and issue.path == "/definitions/0/outputs/value/entries/true"
        for issue in error.value.issues
    )


def test_annual_curve_accepts_a_parameter_at_its_anchor(config):
    data = definition(
        {
            "op": "annual_curve",
            "at": {"op": "input", "name": "a"},
            "points": [
                {"day": "01-01", "value": {"op": "parameter", "name": "winter"}},
                {"day": "07-01", "value": 360},
            ],
        }
    )
    data.update(
        language_version=5,
        parameter_schema={"winter": {"schema": schema("number"), "default": 480}},
    )
    data["inputs"][0]["schema"] = schema("text")
    data["schema"]["fields"]["value"] = schema("number")
    doc = registry(config, data)
    doc.update(config_schema_version=3, fingerprint_version=3)
    declaration = validate(doc, type_registry()).definitions[0]
    day = {"a": value("2026-01-01")}
    output = declaration.outputs["value"]
    assert Evaluation(declaration, day, NOW).evaluate(output).value == 480
    assert (
        Evaluation(declaration, day, NOW, parameters={"winter": 450}).evaluate(output).value == 450
    )


def test_state_machine_duration_uses_an_explicit_or_default_parameter(config):
    data = machine_definition()
    data.update(
        language_version=2,
        parameter_schema={
            "waiting": {"schema": schema("number", unit="s"), "default": 30, "display": "duration"}
        },
    )
    data["machine"]["deadline_s"] = {"op": "parameter", "name": "waiting"}
    doc = registry(config, data)
    declaration = validate(doc, type_registry()).definitions[0]
    assert compile_machine(declaration).definition.deadline_seconds == 30
    assert (
        compile_machine(declaration, parameters={"waiting": 15}).definition.deadline_seconds == 15
    )
