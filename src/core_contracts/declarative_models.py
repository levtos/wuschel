"""Closed, versioned data language for the existing producer engines.

No expression strings are interpreted. The discriminated union is also the
public editor schema; runtime and import use precisely the same models.
"""

from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .lifecycle import Lifecycle
from .model import Model
from .schema import FieldSchema, Schema
from .value_origin import OriginDeclaration

MAX_NODES = 256
MAX_DEPTH = 16
MAX_INPUTS = 64
MAX_FIELDS = 32
MAX_DEFINITIONS = 128
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]


class DeclarativeModel(Model):
    model_config = ConfigDict(serialize_by_alias=True)


class TypedInput(DeclarativeModel):
    name: Identifier
    field_schema: FieldSchema = Field(alias="schema")

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.field_schema.type == "enum" and not self.field_schema.value_catalog:
            raise ValueError("enum_catalog_required")
        return self


class InputRule(Model):
    op: Literal["input"]
    name: Identifier


class ParameterRule(Model):
    op: Literal["parameter"]
    name: Identifier


Duration = Annotated[float, Field(gt=0, le=31_536_000)] | ParameterRule


class ParameterDefinition(DeclarativeModel):
    field_schema: FieldSchema = Field(alias="schema")
    minimum: float | None = None
    maximum: float | None = None
    label: str = ""
    description: str = ""

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.field_schema.type == "object":
            raise ValueError("scalar_parameter_required")
        if self.field_schema.type == "enum" and not self.field_schema.value_catalog:
            raise ValueError("enum_catalog_required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter_bounds")
        if self.field_schema.type != "number" and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("numeric_parameter_bounds_required")
        return self


class ParameterRelation(Model):
    left: Identifier
    comparison: Literal["lt", "le", "eq", "ne", "ge", "gt"]
    right: Identifier


class ReferenceRule(Model):
    op: Literal["ref"]
    name: Identifier


class LiteralRule(DeclarativeModel):
    op: Literal["literal"]
    field_schema: FieldSchema = Field(alias="schema")
    value: Any

    @model_validator(mode="after")
    def typed(self) -> Self:
        if not self.field_schema.accepts(self.value):
            raise ValueError("literal_type_mismatch")
        return self


class BooleanRule(Model):
    op: Literal["and", "or", "not"]
    args: tuple[Rule, ...] = Field(min_length=1, max_length=MAX_INPUTS)

    @model_validator(mode="after")
    def arity(self) -> Self:
        if self.op == "not" and len(self.args) != 1:
            raise ValueError("not_requires_one_argument")
        return self


class CompareRule(Model):
    op: Literal["compare"]
    comparison: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    left: Rule
    right: Rule


class MapRule(DeclarativeModel):
    op: Literal["map"]
    arg: Rule
    field_schema: FieldSchema = Field(alias="schema")
    entries: dict[str, Any] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def typed(self) -> Self:
        if any(not self.field_schema.accepts(value) for value in self.entries.values()):
            raise ValueError("map_type_mismatch")
        return self


class Case(Model):
    when: Rule
    then: Rule


class FirstMatchRule(Model):
    op: Literal["first_match"]
    cases: tuple[Case, ...] = Field(min_length=1, max_length=MAX_INPUTS)


class BucketInitial(DeclarativeModel):
    class_value: Any = Field(default=None, alias="class", exclude_if=lambda value: value is None)
    unknown: bool = False

    @model_validator(mode="after")
    def one_choice(self) -> Self:
        if ("class_value" in self.model_fields_set) == self.unknown:
            raise ValueError("bucket_initial_choice")
        return self


class HysteresisBoundary(Model):
    lower: Rule
    upper: Rule


class BucketBoundary(Model):
    at: Rule
    hysteresis: HysteresisBoundary | None = None


class BucketRule(DeclarativeModel):
    op: Literal["bucket"]
    id: Identifier
    arg: Rule
    initial: bool | BucketInitial
    lower: float | None = Field(default=None, exclude_if=lambda value: value is None)
    upper: float | None = Field(default=None, exclude_if=lambda value: value is None)
    classes: tuple[Any, ...] = Field(default=(), max_length=64, exclude_if=lambda value: not value)
    boundaries: tuple[BucketBoundary, ...] = Field(
        default=(), max_length=63, exclude_if=lambda value: not value
    )
    field_schema: FieldSchema | None = Field(
        default=None, alias="schema", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.classes:
            if (
                len(self.classes) < 2
                or len(self.boundaries) != len(self.classes) - 1
                or self.field_schema is None
            ):
                raise ValueError("bucket_classes_and_boundaries_required")
            if (
                self.lower is not None
                or self.upper is not None
                or not isinstance(self.initial, BucketInitial)
            ):
                raise ValueError("bucket_mode_mismatch")
            if (
                self.field_schema.type == "object"
                or any(not self.field_schema.accepts(v) for v in self.classes)
                or any(a == b for i, a in enumerate(self.classes) for b in self.classes[i + 1 :])
            ):
                raise ValueError("bucket_class_type_mismatch")
            if not self.initial.unknown and self.initial.class_value not in self.classes:
                raise ValueError("bucket_initial_class")
            literal_bands = []
            for boundary in self.boundaries:
                nodes = (
                    [boundary.at, boundary.hysteresis.lower, boundary.hysteresis.upper]
                    if boundary.hysteresis
                    else [boundary.at] * 3
                )
                if all(
                    isinstance(node, LiteralRule) and type(node.value) in {int, float}
                    for node in nodes
                ):
                    literal_bands.append(
                        tuple(node.value for node in nodes if isinstance(node, LiteralRule))
                    )
            if len(literal_bands) == len(self.boundaries) and (
                any(not lower <= at <= upper for at, lower, upper in literal_bands)
                or any(a[2] >= b[1] for a, b in zip(literal_bands, literal_bands[1:]))
            ):
                raise ValueError("invalid_bucket_boundaries")
        elif (
            not isinstance(self.initial, bool)
            or self.lower is None
            or self.upper is None
            or self.lower > self.upper
            or self.boundaries
            or self.field_schema
        ):
            raise ValueError("invalid_hysteresis_bounds")
        return self


class FormulaRule(Model):
    op: Literal["formula"]
    name: str
    version: int = Field(ge=1)
    args: tuple[Rule, ...] | dict[Identifier, Rule] = Field(min_length=1, max_length=MAX_INPUTS)


class FusionRule(Model):
    op: Literal["fusion"]
    strategy: str = Field(min_length=1, max_length=128)
    args: tuple[Rule, ...] = Field(min_length=1, max_length=MAX_INPUTS)


class TemporalRule(Model):
    op: Literal["temporal"]
    id: Identifier
    operation: Literal["stable_for", "dwell", "grace", "edge", "delta", "age"]
    args: tuple[Rule, ...] = Field(min_length=1, max_length=2)
    duration_s: Duration
    accepts_held: bool = False

    @model_validator(mode="after")
    def arity(self) -> Self:
        if len(self.args) != (2 if self.operation == "delta" else 1):
            raise ValueError("temporal_argument_count")
        return self


class MachineRule(Model):
    op: Literal["machine"]


class NotApplicableRule(Model):
    op: Literal["not_applicable"]
    arg: Rule


class TimeReachedRule(Model):
    op: Literal["time_reached"]
    at: Rule


class AnnualCurvePoint(Model):
    day: str = Field(pattern=r"^\d{2}-\d{2}$")
    value: (
        Annotated[int, Field(strict=True)]
        | Annotated[float, Field(strict=True, allow_inf_nan=False)]
        | ParameterRule
    )

    @model_validator(mode="after")
    def calendar_day(self) -> Self:
        date.fromisoformat("2000-" + self.day)
        return self


class AnnualCurveRule(Model):
    op: Literal["annual_curve"]
    at: Rule
    points: tuple[AnnualCurvePoint, ...] = Field(min_length=2, max_length=64)
    leap_day: Literal["skip", "feb28", "mar01"] | None = None

    @model_validator(mode="after")
    def anchors(self) -> Self:
        days = [point.day for point in self.points]
        if days != sorted(set(days)):
            raise ValueError("annual_curve_points_order")
        if "02-29" in days:
            if self.leap_day is None:
                raise ValueError("annual_curve_leap_day_required")
            ordinary = [day for day in days if day != "02-29"]
            if self.leap_day != "skip":
                ordinary.append("02-28" if self.leap_day == "feb28" else "03-01")
            if len(ordinary) < 2 or len(ordinary) != len(set(ordinary)):
                raise ValueError("annual_curve_points_collision")
        return self


class CalendarActiveRule(Model):
    op: Literal["calendar_active"]
    arg: Rule
    pattern: Rule
    all_day_only: bool = False


class KnownAutomaticRule(Model):
    op: Literal["known_automatic"]
    arg: Rule


class ConfigurationHintRule(Model):
    op: Literal["configuration_hint"]
    arg: Rule
    when: Rule
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(default="", max_length=256)


class ManagedCatalogRule(DeclarativeModel):
    op: Literal["managed_catalog_lookup"]
    catalog_id: str
    definition_version: int = Field(ge=1)
    scope: Rule
    media_type: Rule
    signal_type: Rule
    key: Rule
    field_schema: FieldSchema = Field(alias="schema")


class TextRule(Model):
    op: Literal["lower", "strip", "normalize_key", "matches", "contains", "compound_key"]
    args: tuple[Rule, ...] = Field(min_length=1, max_length=MAX_INPUTS)
    delimiters: str = Field(default="_- ", max_length=16)
    separator: str = Field(default=" ", max_length=8)

    @model_validator(mode="after")
    def arity(self) -> Self:
        count = len(self.args)
        if (self.op in {"lower", "strip", "normalize_key"} and count != 1) or (
            self.op in {"matches", "contains"} and count != 2
        ):
            raise ValueError("text_argument_count")
        return self


Rule = Annotated[
    InputRule
    | ParameterRule
    | ReferenceRule
    | LiteralRule
    | BooleanRule
    | CompareRule
    | MapRule
    | FirstMatchRule
    | BucketRule
    | FormulaRule
    | FusionRule
    | TemporalRule
    | MachineRule
    | NotApplicableRule
    | TimeReachedRule
    | AnnualCurveRule
    | CalendarActiveRule
    | KnownAutomaticRule
    | TextRule
    | ConfigurationHintRule
    | ManagedCatalogRule,
    Field(discriminator="op"),
]


class CommandDefinition(Model):
    arguments: dict[Identifier, FieldSchema] = Field(default_factory=dict, max_length=MAX_INPUTS)

    @model_validator(mode="after")
    def complete(self) -> Self:
        if any(s.type == "enum" and not s.value_catalog for s in self.arguments.values()):
            raise ValueError("enum_catalog_required")
        return self


class TransitionDefinition(Model):
    source: Identifier
    target: Identifier
    event: str = Field(min_length=1, max_length=64)
    guard: Rule
    reason: str = Field(min_length=1, max_length=128)
    new_episode: bool = False
    ends_episode: bool = False
    # Equality requirements on typed command arguments; no expression language.
    argument_equals: dict[str, Any] = Field(default_factory=dict, max_length=MAX_INPUTS)
    deadline_action: Literal["keep", "reset", "clear"] = Field(
        default="keep", exclude_if=lambda value: value == "keep"
    )


class StateDeadline(Model):
    duration: Duration


class AbsoluteTransition(Model):
    states: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    target: Identifier
    at_input: Identifier
    priority: Literal["absolute", "duration"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class MachineDefinition(Model):
    id: Identifier
    states: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    initial: Identifier
    transitions: tuple[TransitionDefinition, ...] = Field(max_length=128)
    commands: dict[Identifier, CommandDefinition] = Field(default_factory=dict, max_length=32)
    deadline_s: Duration | None = None
    deadline_by_state: dict[Identifier, StateDeadline] = Field(
        default_factory=dict, max_length=64, exclude_if=lambda value: not value
    )
    absolute_transition: AbsoluteTransition | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    unknown_deadline: Literal["keep_and_block_transition"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sessions: bool = False
    reinitialize_when: Rule
    # Omit the additive false default so released definition fingerprints stay stable.
    allow_manual_restart: bool = Field(
        default=False, strict=True, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.states)) != len(self.states) or self.initial not in self.states:
            raise ValueError("invalid_machine_states")
        if {"input", "deadline"} & self.commands.keys():
            raise ValueError("reserved_machine_event")
        extended = bool(self.deadline_by_state or self.absolute_transition or self.unknown_deadline)
        if extended and (self.deadline_s is not None or self.unknown_deadline is None):
            raise ValueError("explicit_deadline_v2_required")
        if self.deadline_by_state.keys() - set(self.states):
            raise ValueError("unknown_deadline_state")
        absolute = self.absolute_transition
        if absolute:
            if (
                set(absolute.states) - set(self.states)
                or absolute.target not in self.states
                or absolute.target in absolute.states
                or len(set(absolute.states)) != len(absolute.states)
            ):
                raise ValueError("invalid_absolute_transition")
            if absolute.priority is None and any(
                t.source in absolute.states
                and t.event == "deadline"
                and t.target != absolute.target
                for t in self.transitions
            ):
                raise ValueError("deadline_priority_required")
        for declared_command in self.commands.values():
            if any(
                name.startswith("model_") or hasattr(Model, name)
                for name in declared_command.arguments
            ):
                raise ValueError("reserved_command_argument")
        for transition in self.transitions:
            if transition.source not in self.states or transition.target not in self.states:
                raise ValueError("invalid_transition_state")
            if transition.event not in {"input", "deadline", *self.commands}:
                raise ValueError("unknown_transition_event")
            if (
                transition.event == "deadline"
                and self.deadline_s is None
                and transition.source not in self.deadline_by_state
            ):
                raise ValueError("transition_deadline_required")
            if transition.deadline_action != "keep" and (
                not extended or transition.new_episode or transition.ends_episode
            ):
                raise ValueError("ambiguous_deadline_action")
            if transition.deadline_action == "reset" and (
                transition.event != "input" or transition.target not in self.deadline_by_state
            ):
                raise ValueError("reset_duration_required")
            command = self.commands.get(transition.event)
            for name, value in transition.argument_equals.items():
                if not command or name not in command.arguments:
                    raise ValueError("unknown_command_argument")
                if not command.arguments[name].accepts(value):
                    raise ValueError("command_argument_type_mismatch")
        return self


class DeclarativeDefinition(DeclarativeModel):
    type_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*\.[a-zA-Z0-9_.-]+$", max_length=128)
    type_version: int = Field(ge=1)
    language_version: Literal[1, 2, 3, 4, 5] = 1
    display_name: str = ""
    description: str = ""
    inputs: tuple[TypedInput, ...] = Field(default=(), max_length=MAX_INPUTS)
    lifecycle: Lifecycle = Field(default="active", exclude_if=lambda value: value == "active")
    reactivation_note: str | None = Field(
        default=None, max_length=256, exclude_if=lambda value: value is None
    )
    refresh_inputs: tuple[Identifier, ...] = Field(default=(), max_length=MAX_INPUTS)
    output_schema: Schema = Field(alias="schema")
    rules: dict[Identifier, Rule] = Field(default_factory=dict, max_length=MAX_NODES)
    outputs: dict[Identifier, Rule] = Field(min_length=1, max_length=MAX_FIELDS)
    output_origins: dict[Identifier, OriginDeclaration] = Field(
        default_factory=dict, max_length=MAX_FIELDS, exclude_if=lambda value: not value
    )
    machine: MachineDefinition | None = None
    parameter_schema: dict[Identifier, ParameterDefinition] = Field(
        default_factory=dict, max_length=MAX_INPUTS, exclude_if=lambda value: not value
    )
    parameter_relations: tuple[ParameterRelation, ...] = Field(
        default=(), max_length=MAX_INPUTS, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.output_origins and (
            self.language_version < 3
            or self.output_origins.keys() - self.outputs.keys()
            or any(
                origin.kind not in {"model", "replacement"}
                for origin in self.output_origins.values()
            )
        ):
            raise ValueError("explicit_computed_origin_required")
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("duplicate_input")
        if set(self.refresh_inputs) - {item.name for item in self.inputs}:
            raise ValueError("unknown_refresh_input")
        if self.outputs.keys() != self.output_schema.fields.keys():
            raise ValueError("output_schema_mismatch")
        if self.output_schema.grace_capable:
            raise ValueError("declarative_grace_uses_rule_duration")
        if self.parameter_schema and self.language_version < 2:
            raise ValueError("language_version_2_required")
        if (
            self.machine
            and (
                self.machine.deadline_by_state
                or self.machine.absolute_transition
                or self.machine.unknown_deadline
            )
            and self.language_version < 3
        ):
            raise ValueError("language_version_3_required")
        if self.machine and self.machine.absolute_transition:
            declared = next(
                (
                    item
                    for item in self.inputs
                    if item.name == self.machine.absolute_transition.at_input
                ),
                None,
            )
            if declared is None or declared.field_schema.type != "timestamp":
                raise ValueError("absolute_timestamp_input_required")
        for name in self.parameter_schema:
            if name.startswith("model_") or hasattr(Model, name):
                raise ValueError("reserved_parameter_name")
        for relation in self.parameter_relations:
            if (
                relation.left not in self.parameter_schema
                or relation.right not in self.parameter_schema
            ):
                raise ValueError("unknown_parameter_relation")
            left = self.parameter_schema[relation.left].field_schema
            right = self.parameter_schema[relation.right].field_schema
            if left.type != right.type or left.unit != right.unit:
                raise ValueError("parameter_relation_type_mismatch")
            if relation.comparison not in {"eq", "ne"} and left.type not in {
                "number",
                "text",
                "timestamp",
            }:
                raise ValueError("unordered_parameter_relation")
        return self


for _model in (
    BooleanRule,
    CompareRule,
    MapRule,
    Case,
    FirstMatchRule,
    BucketRule,
    BucketBoundary,
    HysteresisBoundary,
    FormulaRule,
    FusionRule,
    TemporalRule,
    TransitionDefinition,
    MachineDefinition,
    NotApplicableRule,
    TimeReachedRule,
    AnnualCurveRule,
    CalendarActiveRule,
    KnownAutomaticRule,
    TextRule,
    ConfigurationHintRule,
    ManagedCatalogRule,
    DeclarativeDefinition,
):
    _model.model_rebuild()
