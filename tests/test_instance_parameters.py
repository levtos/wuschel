import copy

import pytest

from core_contracts.registry import fingerprints
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import definition, registry, schema


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
