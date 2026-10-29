import copy

import pytest

from core_contracts.editorial import CATEGORIES, EditorialWrite
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError, validate_document


def test_devices_category_is_editorial_only():
    value = EditorialWrite.model_validate(
        {
            "object": {"kind": "contract", "id": "fixture.echo"},
            "expected_editorial_version": 0,
            "category": "devices",
        }
    )
    assert value.category == "devices"
    assert CATEGORIES["devices"] == "mdi:devices"


@pytest.mark.parametrize("adapter", ["connectivity", "scheduler"])
def test_adapter_without_origin_rejects_declaration(config, adapter):
    config = copy.deepcopy(config)
    config.update(config_schema_version=2, fingerprint_version=2)
    config["sources"][0]["value_origin"] = {"kind": "measured", "provider": "local"}
    config["bindings"][0]["adapter"] = (
        {"kind": "connectivity"}
        if adapter == "connectivity"
        else {"kind": "scheduler", "calendar": {"projection": "timestamp"}}
    )
    config["bindings"][0].pop("transform")
    with pytest.raises(RegistryValidationError) as caught:
        validate_document(config, type_registry())
    findings = [i for i in caught.value.issues if i.code == "adapter_value_origin_unsupported"]
    assert len(findings) == 1
    assert findings[0].object_id == "source.a"
    assert findings[0].path == "/bindings/0/source_id"
    assert "Entferne die Herkunft" in findings[0].message


async def test_tv_usage_v2_tv_off_is_not_applicable_even_with_missing_source(runtime, config):
    import json
    from pathlib import Path

    from core_contracts.freshness_templates import upgrade_document
    from dev.build_media_templates import schema, tv_usage_v2

    from .conftest import activate
    from .test_beta_media import configuration, observe, source

    definition = tv_usage_v2()
    assert json.loads(
        (Path(__file__).parents[1] / "examples/media-templates-v2.json").read_text(encoding="utf-8")
    ) == [definition]
    doc = configuration(config, "media.tv_usage")
    doc["definitions"] = [definition]
    doc["contracts"][0]["type_version"] = 2
    doc["contracts"][0]["inputs"].append({"name": "applicable", "ref": "source.applicable"})
    applicable_source = source("applicable", schema())
    applicable_source["freshness_selection"] = {
        "kind": "own",
        "rule": applicable_source.pop("freshness"),
    }
    doc["sources"].append(applicable_source)
    doc["bindings"].append(
        {
            "binding_id": "binding.applicable",
            "source_id": "source.applicable",
            "adapter": {"kind": "manual"},
        }
    )
    await activate(runtime, upgrade_document(doc))
    await observe(runtime, "applicable", False)
    assert (
        runtime.snapshot()["contracts"]["fixture.media"]["fields"]["value"]["status"]
        == "not_applicable"
    )
    await observe(runtime, "applicable", True)
    assert (
        runtime.snapshot()["contracts"]["fixture.media"]["fields"]["value"]["status"] == "unknown"
    )
