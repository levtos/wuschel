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
