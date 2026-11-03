from core_contracts.contract_types import type_registry


def test_production_registry_has_no_test_templates():
    assert type_registry().types
    assert not any(key[0].startswith("test.") for key in type_registry().types)
    assert any(key[0].startswith("test.") for key in type_registry(include_test_types=True).types)
