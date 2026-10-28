"""Portable area 6/7 definitions; no real instance configuration in this repository."""

import json
from pathlib import Path

import pytest

from core_contracts.declarative import Evaluation, check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.freshness_templates import upgrade_document
from core_contracts.quality import FieldValue, ReasonCode, unknown
from dev.build_completion_templates import definitions

from .conftest import activate
from .test_core import NOW
from .test_declarative import definition, registry, schema
from .test_phase1a import field


def test_all_portable_completion_definitions_and_reproducibility():
    items = definitions()
    for item in items:
        check_definition(DeclarativeDefinition.model_validate(item))
    assert (
        json.loads(
            (Path(__file__).parents[1] / "examples/completion-templates-v1.json").read_text(
                encoding="utf-8"
            )
        )
        == items
    )
    encoded = json.dumps(items)
    assert "ha_entity_id" not in encoded and "mqtt_topic" not in encoded
    assert len(items) == 14


@pytest.mark.parametrize(
    "place,brightness,phase,expected",
    [
        ("away", "dark", "evening", "active"),
        ("away", "dark", "late_evening", "active"),
        ("away", "dark", "early_night", "active"),
        ("home", None, None, "inactive"),
        ("parents", None, None, "inactive"),
        ("away", "bright", "evening", "inactive"),
        ("away", "dark", "morning", "inactive"),
        (None, "dark", "evening", None),
    ],
)
def test_simulation_uses_existing_facts_and_keeps_missing_information(
    place, brightness, phase, expected
):
    data = DeclarativeDefinition.model_validate(
        next(d for d in definitions() if d["type_id"] == "simulation.state")
    )
    inputs = {
        name: FieldValue(status="valid", quality="healthy", value=value)
        if value is not None
        else unknown(ReasonCode.INPUT_ABSENT, name, NOW)
        for name, value in zip(
            ["location", "brightness", "phase"], [place, brightness, phase], strict=True
        )
    }
    result = Evaluation(data, inputs, NOW).evaluate(data.outputs["value"])
    assert result.value == expected
    assert result.status == ("unresolved" if expected is None else "valid")


async def test_existing_connectivity_adapter_projects_mqtt_dependency_without_ha(runtime, config):
    data = definition()
    shape = schema("enum", value_catalog=["ready", "starting", "unavailable"])
    data["inputs"][0]["schema"] = shape
    data["schema"]["fields"]["value"] = shape
    doc = registry(config, data)
    doc["dependencies"][0].update(kind="mqtt")
    doc["bindings"][0].update(adapter={"kind": "connectivity"}, transform=None)
    await activate(runtime, upgrade_document(doc))
    assert not runtime.requires_ha_snapshot
    for status, expected in [
        ("connected", "ready"),
        ("disconnected", "unavailable"),
        ("connecting", "starting"),
        ("connected", "ready"),
    ]:
        await runtime.submit("mqtt_state", status)
        assert field(runtime)["value"] == expected
