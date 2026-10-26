"""Closed instance configuration, shared by activation, evaluation and scheduling."""

import operator
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model, model_validator

from . import declarative_models as dm
from .model import Model

COMPARISONS = {
    "lt": operator.lt,
    "le": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
    "ge": operator.ge,
    "gt": operator.gt,
}


def parameter_model(definition: dm.DeclarativeDefinition) -> type[BaseModel]:
    class Parameters(Model):
        @model_validator(mode="after")
        def values_and_relations(self) -> Parameters:
            values = self.model_dump(exclude_unset=True)
            for key, value in values.items():
                if not definition.parameter_schema[key].field_schema.accepts(value):
                    raise ValueError("parameter_type_mismatch")
            for relation in definition.parameter_relations:
                if relation.left not in values or relation.right not in values:
                    raise ValueError("parameter_relation_failed")
                left, right = values[relation.left], values[relation.right]
                if (
                    definition.parameter_schema[relation.left].field_schema.type == "timestamp"
                    and left is not None
                    and right is not None
                ):
                    left, right = datetime.fromisoformat(left), datetime.fromisoformat(right)
                if not COMPARISONS[relation.comparison](left, right):
                    raise ValueError("parameter_relation_failed")
            return self

    fields: dict[str, Any] = {}
    for name, declaration in definition.parameter_schema.items():
        schema = declaration.field_schema
        annotation: Any = {
            "number": float,
            "bool": bool,
            "text": str,
            "enum": str,
            "timestamp": str,
            "object": dict[str, Any],
        }[schema.type]
        if declaration.structured_schema == "wake_planning_v1":
            from .wake.settings import WakeSettings

            annotation = WakeSettings
        if schema.value_catalog:
            annotation = Literal[tuple(schema.value_catalog)]
        fields[name] = (
            annotation,
            Field(
                default=... if schema.required else None,
                strict=None if schema.value_catalog else True,
                title=declaration.label or name,
                description=declaration.description,
                ge=declaration.minimum,
                le=declaration.maximum,
                json_schema_extra={
                    "unit": schema.unit,
                    **({"format": "date-time"} if schema.type == "timestamp" else {}),
                },
            ),
        )
    return create_model("InstanceParameters", __base__=Parameters, **fields)


def number_slot(value: float | dm.ParameterRule, parameters: dict[str, Any]) -> float:
    result = parameters[value.name] if isinstance(value, dm.ParameterRule) else value
    if type(result) not in {int, float}:
        raise ValueError("numeric_parameter_required")
    return float(result)


def validate_slots(definition: dm.DeclarativeDefinition, parameters: dict[str, Any]) -> None:
    # Import only at validation time: the compiler itself uses parameter_model.
    from .declarative import all_rules

    for rule in all_rules(definition):
        if isinstance(rule, dm.TemporalRule):
            seconds = number_slot(rule.duration_s, parameters)
            if not 0 < seconds <= 31_536_000:
                raise ValueError("duration_out_of_bounds")
    if definition.machine and definition.machine.deadline_s is not None:
        if not 0 < number_slot(definition.machine.deadline_s, parameters) <= 31_536_000:
            raise ValueError("duration_out_of_bounds")
    if definition.machine:
        for deadline in definition.machine.deadline_by_state.values():
            if not 0 < number_slot(deadline.duration, parameters) <= 31_536_000:
                raise ValueError("duration_out_of_bounds")
