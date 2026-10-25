"""Code-owned engine catalog shared by discovery, typing and evaluation."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .fusion import Fusion
from .lifecycle import Lifecycle, transition_allowed
from .quality import FieldValue, ReasonCode, derive, unknown
from .resolver import FormulaCatalog
from .schema import FieldSchema


@dataclass(frozen=True)
class FormulaInput:
    name: str
    schema: FieldSchema
    quantity: str
    required: bool = True


@dataclass(frozen=True)
class FormulaSchema:
    input: FieldSchema
    output: FieldSchema
    inputs: tuple[FormulaInput, ...] = ()
    category: str = "test"
    lifecycle: Lifecycle = "active"


FORMULAS = FormulaCatalog()
FORMULA_SCHEMAS: dict[tuple[str, int], FormulaSchema] = {}
FUSION = Fusion()
FUSION_INPUTS: dict[str, str | None] = {
    "first_healthy": None,
    "first_usable": None,
    "latest": None,
    "any_true": "bool",
    "all_true": "bool",
}


def register_formula(
    name: str,
    version: int,
    implementation: Any,
    input_schema: FieldSchema,
    output_schema: FieldSchema,
    *,
    inputs: tuple[FormulaInput, ...] = (),
    category: str = "test",
    lifecycle: Lifecycle = "active",
) -> None:
    if (
        not category.strip()
        or len({i.name for i in inputs}) != len(inputs)
        or any(not i.name or not i.quantity for i in inputs)
    ):
        raise ValueError("invalid_formula_declaration")
    FORMULAS.register(name, version, implementation)
    FORMULA_SCHEMAS[name, version] = FormulaSchema(
        input_schema, output_schema, inputs, category, lifecycle
    )


def set_formula_lifecycle(
    name: str, version: int, state: Lifecycle, *, reactivation_note: str | None = None
) -> None:
    current = FORMULA_SCHEMAS[name, version]
    if not transition_allowed(current.lifecycle, state, reactivation_note, None):
        raise ValueError("lifecycle_transition")
    FORMULA_SCHEMAS[name, version] = replace(current, lifecycle=state)


def register_fusion(name: str, implementation: Any, input_type: str | None = None) -> None:
    FUSION.register(name, implementation)
    FUSION_INPUTS[name] = input_type


def agreement(items: list[FieldValue], at: datetime) -> FieldValue:
    if any(not item.usable(at) for item in items):
        return unknown(ReasonCode.INPUT_UNKNOWN, "candidates", at)
    if any(item.value != items[0].value for item in items):
        return unknown(ReasonCode.CONFLICT, "candidates", at)
    return derive(items[0].value, items, at)


_number = FieldSchema(type="number", required=True, physical_state=False)
register_formula("test.sum", 1, sum, _number, _number)
register_fusion("test_agreement", agreement)
