"""Application type registry: production types are independent of test evaluators."""

from core_contracts.extensions.openings import register

from .manual_change import register as register_manual_change
from .schema import TypeRegistry


def type_registry(*, include_test_types: bool = False) -> TypeRegistry:
    registry = TypeRegistry()
    if include_test_types:
        from .testing.contract_types import type_registry as fixture_types

        registry = fixture_types()
    register(registry)
    register_manual_change(registry)
    return registry
