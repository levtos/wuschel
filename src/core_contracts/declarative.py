"""Type-check and adapt declarative data to the existing Producer lifecycle."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model, model_validator

from . import declarative_models as dm
from .engines import FORMULA_SCHEMAS, FORMULAS, FUSION, FUSION_INPUTS
from .instance_parameters import number_slot, parameter_model
from .model import Model
from .multibucket import MultiBucket
from .producer import Context, Producer, TemporalParameters, next_machine_due, next_temporal_due
from .producer import state_machine as evaluate_machine
from .producer import temporal as evaluate_temporal
from .quality import ConfigurationHint, ConfigurationStatus, FieldValue, ReasonCode, derive, unknown
from .resolver import Bucket, boolean, compare, first_match
from .schema import ContractType, FieldSchema, InputDeclaration
from .statemachine import Definition, Guard, Machine, Transition
from .text_rules import evaluate_text


class EmptyParameters(Model):
    """Definition versions contain semantic parameters; instances bind inputs."""


class RuleValidationError(ValueError):
    def __init__(self, code: str, path: str) -> None:
        self.path = path
        super().__init__(code)


class DefinitionValidationErrors(ValueError):
    def __init__(self, issues: list[RuleValidationError]) -> None:
        self.issues = issues
        super().__init__(str(issues[0]))


class GuardEvaluationError(Exception):
    """A local data/operation failure in a read-only declarative guard."""


def map_key(value: Any) -> str:
    """Canonical JSON scalar keys; numeric equality includes 42 == 42.0."""
    if type(value) in {int, float} and isfinite(value):
        number = format(Decimal(str(value)), "f")
        return (number.rstrip("0").rstrip(".") if "." in number else number) if value else "0"
    if type(value) in {str, bool}:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise ValueError("invalid_map_key")


def rule_children(rule: dm.Rule) -> list[tuple[str, dm.Rule]]:
    if isinstance(rule, dm.ManagedCatalogRule):
        return [
            (name, getattr(rule, name)) for name in ("scope", "media_type", "signal_type", "key")
        ]
    if isinstance(rule, dm.ConfigurationHintRule):
        return [("arg", rule.arg), ("when", rule.when)]
    if isinstance(rule, dm.FormulaRule):
        return (
            [(f"args/{name}", child) for name, child in rule.args.items()]
            if isinstance(rule.args, dict)
            else [(f"args/{index}", child) for index, child in enumerate(rule.args)]
        )
    if isinstance(rule, dm.BucketRule) and rule.classes:
        result = [("arg", rule.arg)]
        for index, boundary in enumerate(rule.boundaries):
            result.append((f"boundaries/{index}/at", boundary.at))
            if boundary.hysteresis:
                result.extend(
                    [
                        (f"boundaries/{index}/hysteresis/lower", boundary.hysteresis.lower),
                        (f"boundaries/{index}/hysteresis/upper", boundary.hysteresis.upper),
                    ]
                )
        return result
    if isinstance(rule, dm.TimeReachedRule):
        return [("at", rule.at)]
    if isinstance(rule, dm.CompareRule):
        return [("left", rule.left), ("right", rule.right)]
    if isinstance(rule, (dm.MapRule, dm.BucketRule, dm.NotApplicableRule, dm.KnownAutomaticRule)):
        return [("arg", rule.arg)]
    if isinstance(rule, dm.FirstMatchRule):
        return [
            (f"cases/{index}/{name}", child)
            for index, case in enumerate(rule.cases)
            for name, child in (("when", case.when), ("then", case.then))
        ]
    if isinstance(rule, (dm.BooleanRule, dm.FusionRule, dm.TemporalRule, dm.TextRule)):
        return [(f"args/{index}", child) for index, child in enumerate(rule.args)]
    return []


def scalar(kind: Literal["bool", "number", "text", "enum"]) -> FieldSchema:
    return FieldSchema(type=kind, required=True, physical_state=False)


def compatible(actual: FieldSchema, expected: FieldSchema) -> bool:
    return (
        (actual.type == expected.type or (actual.type == "enum" and expected.type == "text"))
        and (
            not expected.value_catalog
            or (
                bool(actual.value_catalog)
                and set(actual.value_catalog) <= set(expected.value_catalog)
            )
        )
        and (expected.unit is None or actual.unit == expected.unit)
    )


def joined(schemas: list[FieldSchema]) -> FieldSchema:
    first = schemas[0]
    if len({s.unit for s in schemas}) > 1:
        raise ValueError("unit_mismatch")
    kinds = {s.type for s in schemas}
    if kinds <= {"enum", "text"} and "text" in kinds:
        return scalar("text").model_copy(update={"unit": first.unit})
    if len(kinds) != 1:
        raise ValueError("rule_type_mismatch")
    if first.type == "enum":
        return first.model_copy(
            update={"value_catalog": tuple(sorted({v for s in schemas for v in s.value_catalog}))}
        )
    return first


def children(rule: dm.Rule) -> tuple[dm.Rule, ...]:
    return tuple(child for _, child in rule_children(rule))


def all_rules(definition: dm.DeclarativeDefinition) -> list[dm.Rule]:
    roots = [*definition.rules.values(), *definition.outputs.values()]
    if definition.machine:
        roots += [t.guard for t in definition.machine.transitions]
        roots.append(definition.machine.reinitialize_when)
    result: list[dm.Rule] = []

    def visit(rule: dm.Rule, depth: int) -> None:
        if depth > dm.MAX_DEPTH or len(result) >= dm.MAX_NODES:
            raise ValueError("definition_resource_limit")
        result.append(rule)
        for child in children(rule):
            visit(child, depth + 1)

    for root in roots:
        visit(root, 1)
    return result


def check_definition(definition: dm.DeclarativeDefinition) -> None:
    rules = all_rules(definition)
    slots = [r.duration_s for r in rules if isinstance(r, dm.TemporalRule)]
    if definition.machine and definition.machine.deadline_s is not None:
        slots.append(definition.machine.deadline_s)
    if definition.machine:
        slots.extend(item.duration for item in definition.machine.deadline_by_state.values())
    for slot in slots:
        if isinstance(slot, dm.ParameterRule):
            declaration = definition.parameter_schema.get(slot.name)
            if (
                not declaration
                or declaration.field_schema.type != "number"
                or not declaration.field_schema.required
            ):
                raise ValueError("invalid_parameter_slot")
    paths: dict[int, str] = {}

    def locate(rule: dm.Rule, path: str) -> None:
        paths[id(rule)] = path
        for suffix, child in rule_children(rule):
            locate(child, path + "/" + suffix)

    for group in ("rules", "outputs"):
        for name, rule in getattr(definition, group).items():
            locate(rule, f"/{group}/{name}")
    if definition.machine:
        locate(definition.machine.reinitialize_when, "/machine/reinitialize_when")
        for index, transition in enumerate(definition.machine.transitions):
            locate(transition.guard, f"/machine/transitions/{index}/guard")
    identifiers = [r.id for r in rules if isinstance(r, (dm.BucketRule, dm.TemporalRule))]
    if definition.machine:
        identifiers.append(definition.machine.id)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate_stateful_id")
    inputs = {item.name: item.field_schema for item in definition.inputs}
    visiting: set[str] = set()
    resolved: dict[tuple[str, bool], FieldSchema] = {}
    depths: dict[str, int] = {}

    def expanded_depth(rule: dm.Rule, active: frozenset[str] = frozenset()) -> int:
        if isinstance(rule, dm.ReferenceRule):
            if rule.name in active:
                raise ValueError("internal_cycle")
            if rule.name not in definition.rules:
                raise ValueError("unknown_internal_reference")
            if rule.name not in depths:
                depths[rule.name] = expanded_depth(
                    definition.rules[rule.name], active | {rule.name}
                )
            return 1 + depths[rule.name]
        return 1 + max((expanded_depth(child, active) for child in children(rule)), default=0)

    if any(expanded_depth(rule) > dm.MAX_DEPTH for rule in rules):
        raise ValueError("definition_resource_limit")

    def require(actual: FieldSchema, expected: FieldSchema) -> FieldSchema:
        if not compatible(actual, expected):
            raise ValueError("rule_type_mismatch")
        return actual

    def infer(rule: dm.Rule, *, guard: bool = False, depth: int = 0) -> FieldSchema:
        try:
            return infer_rule(rule, guard=guard, depth=depth)
        except RuleValidationError:
            raise
        except ValueError as error:
            raise RuleValidationError(str(error), paths.get(id(rule), "/rules")) from None

    def infer_rule(rule: dm.Rule, *, guard: bool = False, depth: int = 0) -> FieldSchema:
        if depth > dm.MAX_DEPTH:
            raise ValueError("definition_resource_limit")
        if guard and isinstance(rule, (dm.MachineRule, dm.TemporalRule, dm.BucketRule)):
            raise ValueError("stateful_guard_requires_contract_input")
        if isinstance(rule, dm.InputRule):
            if rule.name not in inputs:
                raise ValueError("unknown_rule_input")
            return inputs[rule.name]
        if isinstance(rule, dm.ParameterRule):
            parameter_declaration = definition.parameter_schema.get(rule.name)
            if (
                definition.language_version < 2
                or not parameter_declaration
                or not parameter_declaration.field_schema.required
            ):
                raise ValueError("unknown_or_optional_parameter")
            return parameter_declaration.field_schema
        if isinstance(rule, dm.ReferenceRule):
            if rule.name not in definition.rules:
                raise ValueError("unknown_internal_reference")
            if rule.name in visiting:
                raise ValueError("internal_cycle")
            key = (rule.name, guard)
            if key not in resolved:
                visiting.add(rule.name)
                try:
                    resolved[key] = infer(definition.rules[rule.name], guard=guard, depth=depth + 1)
                finally:
                    visiting.remove(rule.name)
            return resolved[key]
        if isinstance(rule, dm.LiteralRule):
            return rule.field_schema
        args = [infer(child, guard=guard, depth=depth + 1) for child in children(rule)]
        if isinstance(rule, dm.KnownAutomaticRule):
            if definition.language_version < 3:
                raise ValueError("language_version_3_required")
            return scalar("bool")
        if isinstance(rule, dm.ConfigurationHintRule):
            if definition.language_version < 2:
                raise ValueError("language_version_2_required")
            require(args[1], scalar("bool"))
            return args[0]
        if isinstance(rule, dm.TextRule):
            if definition.language_version < 2:
                raise ValueError("language_version_2_required")
            for arg in args:
                require(arg, scalar("text"))
            return scalar("bool" if rule.op in {"contains", "matches"} else "text")
        if isinstance(rule, dm.TimeReachedRule):
            if definition.language_version < 2:
                raise ValueError("language_version_2_required")
            require(args[0], FieldSchema(type="timestamp", required=True, physical_state=False))
            return scalar("bool")
        if isinstance(rule, dm.BooleanRule):
            for arg in args:
                require(arg, scalar("bool"))
            return scalar("bool")
        if isinstance(rule, dm.NotApplicableRule):
            require(args[0], scalar("bool"))
            return scalar("bool")
        if isinstance(rule, dm.CompareRule):
            if not (compatible(args[0], args[1]) or compatible(args[1], args[0])):
                raise ValueError("comparison_type_mismatch")
            if rule.comparison not in {"eq", "ne"} and args[0].type not in {
                "number",
                "text",
                "timestamp",
            }:
                raise ValueError("unordered_comparison")
            return scalar("bool")
        if isinstance(rule, dm.ManagedCatalogRule):
            if definition.language_version != 4:
                raise ValueError("language_version_4_required")
            for arg in args:
                require(arg, scalar("text"))
            if rule.field_schema.type != "enum" or not rule.field_schema.value_catalog:
                raise ValueError("catalog_classification_schema")
            return rule.field_schema
        if isinstance(rule, dm.MapRule):
            if args[0].type == "object":
                raise ValueError("map_requires_scalar")
            for entry_key in rule.entries:
                try:
                    value = json.loads(entry_key)
                    if not args[0].accepts(value) or map_key(value) != entry_key:
                        raise ValueError("invalid_map_key")
                except ValueError, TypeError, OverflowError:
                    escaped = entry_key.replace("~", "~0").replace("/", "~1")
                    raise RuleValidationError(
                        "invalid_map_key", paths[id(rule)] + "/entries/" + escaped
                    ) from None
            return rule.field_schema
        if isinstance(rule, dm.FirstMatchRule):
            for arg in args[::2]:
                require(arg, scalar("bool"))
            return joined(args[1::2])
        if isinstance(rule, dm.BucketRule):
            require(args[0], scalar("number"))
            if rule.classes:
                if definition.language_version < 2:
                    raise ValueError("language_version_2_required")
                for arg in args[1:]:
                    require(arg, args[0])
                    if arg.unit != args[0].unit:
                        raise ValueError("unit_mismatch")
                assert rule.field_schema is not None
                return rule.field_schema
            return scalar("bool")
        if isinstance(rule, dm.FormulaRule):
            if (rule.name, rule.version) not in FORMULA_SCHEMAS:
                raise ValueError("unknown_formula_version")
            declaration = FORMULA_SCHEMAS[rule.name, rule.version]
            if declaration.inputs:
                if definition.language_version < 2 or not isinstance(rule.args, dict):
                    raise ValueError("named_formula_arguments_required")
                specifications = {item.name: item for item in declaration.inputs}
                if set(rule.args) - specifications.keys() or any(
                    item.required and item.name not in rule.args for item in declaration.inputs
                ):
                    raise ValueError("formula_argument_names")
                for name, arg in zip(rule.args, args, strict=True):
                    require(arg, specifications[name].schema)
                    if arg.unit != specifications[name].schema.unit:
                        raise ValueError("unit_mismatch")
                return declaration.output
            if isinstance(rule.args, dict):
                raise ValueError("positional_formula_arguments_required")
            for arg in args:
                require(arg, declaration.input)
            units = {arg.unit for arg in args}
            if len(units) != 1:
                raise ValueError("unit_mismatch")
            return declaration.output.model_copy(update={"unit": args[0].unit})
        if isinstance(rule, dm.FusionRule):
            if rule.strategy not in FUSION_INPUTS:
                raise ValueError("unknown_fusion_strategy")
            kind = FUSION_INPUTS[rule.strategy]
            expected = (
                FieldSchema.model_validate(
                    {"type": kind, "required": True, "physical_state": False}
                )
                if kind
                else joined(args)
            )
            for arg in args:
                require(arg, expected)
            return expected
        if isinstance(rule, dm.TemporalRule):
            if rule.operation in {"stable_for", "dwell", "edge"}:
                require(args[0], scalar("bool"))
                return scalar("bool")
            if rule.operation == "delta":
                for arg in args:
                    require(arg, scalar("number"))
                return scalar("number")
            return scalar("number") if rule.operation == "age" else args[0]
        assert isinstance(rule, dm.MachineRule)
        if not definition.machine:
            raise ValueError("machine_definition_required")
        return scalar("enum").model_copy(update={"value_catalog": definition.machine.states})

    issues: list[RuleValidationError] = []

    def check(rule: dm.Rule, expected: FieldSchema | None = None, *, guard: bool = False) -> None:
        try:
            actual = infer(rule, guard=guard)
            if expected and not compatible(actual, expected):
                raise RuleValidationError("rule_type_mismatch", paths.get(id(rule), "/rules"))
        except RuleValidationError as error:
            if not any((str(item), item.path) == (str(error), error.path) for item in issues):
                issues.append(error)

    for rule in definition.rules.values():
        check(rule)
    for name, rule in definition.outputs.items():
        check(rule, definition.output_schema.fields[name])
    if definition.machine:
        seen: set[tuple[str, str]] = set()
        for index, transition in enumerate(definition.machine.transitions):
            pair = (transition.source, transition.event)
            if pair in seen:
                issues.append(
                    RuleValidationError(
                        "duplicate_transition_event", f"/machine/transitions/{index}/event"
                    )
                )
            seen.add(pair)

        reachable: dict[str, bool] = {}

        def reaches_machine(rule: dm.Rule) -> bool:
            if isinstance(rule, dm.MachineRule):
                return True
            if isinstance(rule, dm.ReferenceRule):
                # References and cycles were checked above. Cache false results too:
                # a shared DAG must not expand into every possible reference path.
                if rule.name not in reachable:
                    reachable[rule.name] = reaches_machine(definition.rules[rule.name])
                return reachable[rule.name]
            return any(reaches_machine(child) for child in children(rule))

        if not any(reaches_machine(rule) for rule in definition.outputs.values()):
            issues.append(RuleValidationError("unreachable_machine", "/machine"))
        for rule in [definition.machine.reinitialize_when] + [
            t.guard for t in definition.machine.transitions
        ]:
            check(rule, scalar("bool"), guard=True)
    if issues:
        raise DefinitionValidationErrors(issues)


class Evaluation:
    """One bounded invocation; named rules and stateful nodes evaluate once."""

    def __init__(
        self,
        definition: dm.DeclarativeDefinition,
        inputs: dict[str, FieldValue],
        now: datetime,
        context: Context | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.definition, self.inputs, self.now, self.context = definition, inputs, now, context
        self.parameters = (
            parameters if parameters is not None else context.contract.parameters if context else {}
        )
        self.inputs = dict(inputs)
        for declaration in definition.inputs:
            value = self.inputs.get(declaration.name)
            if value and value.usable(now) and not declaration.field_schema.accepts(value.value):
                self.inputs[declaration.name] = unknown(
                    ReasonCode.INVALID_VALUE, declaration.name, now
                )
        self.memo: dict[str, FieldValue] = {}
        self.machine_reads: list[FieldValue] = []
        if context is not None:
            context.node.pop("catalog_lookups", None)
        self.trace: list[dict[str, Any]] = []
        self.cost = 0

    def machine_evidence(self, value: FieldValue) -> None:
        from .managed_catalog import dependencies, suspend_machine

        if self.context is not None:
            slot = self.context.node.setdefault("catalog_machine", {})
            self.machine_reads.append(value)
            if dependencies(slot, [value]):
                suspend_machine(self.context.node)

    def evaluate(self, rule: dm.Rule) -> FieldValue:
        self.cost += 1
        if self.cost > dm.MAX_NODES * 2:
            raise ValueError("evaluation_resource_limit")
        result = self._evaluate(rule)
        if (
            result.usable(self.now)
            and type(result.value) in {int, float}
            and not isfinite(result.value)
        ):
            raise ValueError("declarative_nonfinite_result")
        # No raw values, attributes, parameters or command arguments in traces.
        self.trace.append(
            {
                "op": rule.op,
                "id": getattr(rule, "id", None),
                "status": result.status,
                "quality": result.quality,
                "reasons": [reason.code.value for reason in result.reasons],
                "evidence": list(result.evidence[:64]),
                **(
                    {"name": rule.name}
                    if isinstance(rule, (dm.InputRule, dm.ReferenceRule, dm.FormulaRule))
                    else {}
                ),
                **({"strategy": rule.strategy} if isinstance(rule, dm.FusionRule) else {}),
            }
        )
        return result

    def _evaluate(self, rule: dm.Rule) -> FieldValue:
        now = self.now
        if isinstance(rule, dm.InputRule):
            return self.inputs.get(rule.name, unknown(ReasonCode.INPUT_ABSENT, rule.name, now))
        if isinstance(rule, dm.ParameterRule):
            return FieldValue(status="valid", value=self.parameters[rule.name], quality="healthy")
        if isinstance(rule, dm.ReferenceRule):
            if rule.name not in self.memo:
                self.memo[rule.name] = self.evaluate(self.definition.rules[rule.name])
            return self.memo[rule.name]
        if isinstance(rule, dm.LiteralRule):
            return FieldValue(status="valid", value=rule.value, quality="healthy")
        if isinstance(rule, dm.MachineRule):
            if "$machine" not in self.memo:
                assert self.context is not None and self.definition.machine is not None
                reinitialize_rule = self.definition.machine.reinitialize_when

                def reinitialize(ctx: Context) -> bool:
                    condition = self.evaluate(reinitialize_rule)
                    self.machine_evidence(condition)
                    allowed = (
                        condition.status == "valid"
                        and condition.value is True
                        and bool(condition.evidence)
                    )
                    names: set[str] = set()
                    visited: set[str] = set()

                    def collect(rule: dm.Rule) -> None:
                        if isinstance(rule, dm.InputRule):
                            names.add(rule.name)
                        elif isinstance(rule, dm.ReferenceRule) and rule.name not in visited:
                            visited.add(rule.name)
                            collect(self.definition.rules[rule.name])
                        for child in children(rule):
                            collect(child)

                    collect(reinitialize_rule)
                    ctx.node["machine_recovery"] = {
                        "status": condition.status,
                        "quality": condition.quality,
                        "condition_met": allowed,
                        "evidence": list(condition.evidence[:64]) if allowed else [],
                        "inputs": [
                            {
                                "name": name,
                                "status": self.inputs[name].status
                                if name in self.inputs
                                else "unknown",
                                "reasons": [r.code.value for r in self.inputs[name].reasons]
                                if name in self.inputs
                                else ["input_absent"],
                            }
                            for name in sorted(names)
                        ],
                    }
                    return allowed

                if self.context.override is not None:
                    previous = self.context.node.get("machine", {}).get("state")
                    for transition in self.definition.machine.transitions:
                        if (
                            transition.source == previous
                            and transition.event == self.context.override.trigger
                        ):
                            guard = self.evaluate(transition.guard)
                            self.machine_evidence(guard)
                            self.trace.append(
                                {
                                    "op": "guard",
                                    "source": previous,
                                    "target": transition.target,
                                    "event": transition.event,
                                    "decision": guard.value if guard.usable(now) else None,
                                }
                            )
                            break
                before_episode = (
                    self.context.node.get("machine")
                    or self.context.node.get("catalog_machine_pending", {})
                ).get("episode_id")
                self.memo["$machine"] = evaluate_machine(
                    self.context,
                    compile_machine(self.definition, evaluation=self),
                    reinitialize=reinitialize,
                )
                slot = self.context.node.setdefault("catalog_machine", {})
                after_episode = self.context.node.get("machine", {}).get("episode_id")
                if after_episode and after_episode != before_episode:
                    from .managed_catalog import dependencies

                    slot.clear()
                    dependencies(slot, self.machine_reads)
                refs = tuple(
                    f"{key}:{digest}"
                    for key, digest in slot.get("catalog_dependencies", {}).items()
                )
                value = self.memo["$machine"]
                self.memo["$machine"] = value.model_copy(
                    update={"evidence": tuple(dict.fromkeys((*value.evidence, *refs)))}
                )
            return self.memo["$machine"]
        args = [self.evaluate(child) for child in children(rule)]
        if isinstance(rule, dm.ManagedCatalogRule):
            from .managed_catalog import lookup

            assert self.context is not None
            return lookup(self.context, rule, args)
        if isinstance(rule, dm.KnownAutomaticRule):
            return derive(
                bool(args[0].attribution and args[0].attribution.kind == "known_automatic"),
                args,
                now,
            )
        if isinstance(rule, dm.ConfigurationHintRule):
            result = args[0]
            if not args[1].usable(now) or args[1].value is True:
                hint = ConfigurationHint(
                    code=rule.code if args[1].usable(now) else "configuration_check_unavailable",
                    message=rule.message if args[1].usable(now) else "",
                )
                result = result.model_copy(
                    update={
                        "configuration": ConfigurationStatus(
                            complete=False,
                            hints=tuple(dict.fromkeys((*result.configuration.hints, hint))),
                        )
                    }
                )
            return result
        if isinstance(rule, dm.TextRule):
            return evaluate_text(rule, args, now)
        if isinstance(rule, dm.TimeReachedRule):
            if not args[0].usable(now):
                return derive(None, args, now)
            target = datetime.fromisoformat(args[0].value)
            if self.context and target > now:
                self.context.node.setdefault("clock_due", []).append(target.isoformat())
            return derive(now >= target, args, now)
        if isinstance(rule, dm.BooleanRule):
            return boolean(rule.op, args, now)
        if isinstance(rule, dm.NotApplicableRule):
            return (
                FieldValue(
                    status="not_applicable",
                    quality="healthy",
                    positively_not_applicable=True,
                    evidence=args[0].evidence,
                )
                if args[0].usable(now) and args[0].value is True
                else args[0]
            )
        if isinstance(rule, dm.CompareRule):
            return compare(args[0], args[1], rule.comparison, now)
        if isinstance(rule, dm.MapRule):
            value = args[0]
            if not value.usable(now):
                return value
            key = map_key(value.value)
            if key not in rule.entries:
                return unknown(ReasonCode.UNMAPPED_VALUE, "map", now)
            return derive(rule.entries[key], [value], now)
        if isinstance(rule, dm.FirstMatchRule):
            return first_match(list(zip(args[::2], args[1::2], strict=True)), now)
        if isinstance(rule, dm.FormulaRule):
            return FORMULAS.evaluate(
                rule.name,
                rule.version,
                args,
                now,
                raise_errors=True,
                names=tuple(rule.args) if isinstance(rule.args, dict) else None,
            )
        if isinstance(rule, dm.FusionRule):
            result = FUSION.evaluate(rule.strategy, args, now)
            usable = [index for index, arg in enumerate(args) if arg.usable(now)]
            selected: list[int] | None = None
            if rule.strategy == "first_usable":
                selected = usable[:1]
            elif rule.strategy == "first_healthy":
                selected = (
                    [next((i for i in usable if args[i].quality == "healthy"), usable[0])]
                    if usable
                    else []
                )
            elif rule.strategy == "latest":
                timed = [i for i in usable if args[i].measured_at is not None]
                stamp = max(
                    (
                        arg.measured_at
                        for arg in args
                        if arg.usable(now) and arg.measured_at is not None
                    ),
                    default=None,
                )
                selected = [i for i in timed if args[i].measured_at == stamp]
            elif rule.strategy in {"any_true", "all_true"}:
                decisive = rule.strategy == "any_true"
                selected = [i for i in usable if args[i].value is decisive] or list(
                    range(len(args))
                )
            self.trace.append(
                {
                    "op": "fusion_selection",
                    "strategy": rule.strategy,
                    "selected_indices": selected,
                    "candidate_statuses": [arg.status for arg in args],
                    "selection": "engine" if selected is not None else "registered_strategy",
                    "evidence": list(result.evidence[:64]),
                }
            )
            return result
        assert self.context is not None
        if isinstance(rule, dm.BucketRule):
            key = f"{self.context.contract.contract_id}:{rule.id}"
            from .managed_catalog import dependencies

            slot = self.context.node.setdefault("catalog_buckets", {}).setdefault(rule.id, {})
            if dependencies(slot, args):
                self.context.latches.pop(key, None)
            # G6: hysteresis is volatile, including after database recovery. Remove
            # pre-review Alpha-4 context without touching other temporal nodes.
            self.context.node.get("rules", {}).pop(rule.id, None)
            if rule.classes:
                assert isinstance(rule.initial, dm.BucketInitial)
                initial = (
                    None if rule.initial.unknown else rule.classes.index(rule.initial.class_value)
                )
                multi = self.context.latches.setdefault(key, MultiBucket(rule.classes, initial))
                assert isinstance(multi, MultiBucket)
                bands: list[tuple[int, int, int]] = []
                pos = 1
                for boundary in rule.boundaries:
                    bands.append(
                        (pos, pos + 1, pos + 2) if boundary.hysteresis else (pos, pos, pos)
                    )
                    pos += 3 if boundary.hysteresis else 1
                return multi.classify(args, bands, now)
            assert (
                isinstance(rule.initial, bool) and rule.lower is not None and rule.upper is not None
            )
            bucket = self.context.latches.setdefault(
                key, Bucket(rule.initial, rule.lower, rule.upper)
            )
            return bucket.evaluate(args[0], now)
        assert isinstance(rule, dm.TemporalRule)
        node = self.context.node.setdefault("rules", {}).setdefault(
            rule.id, {"fingerprint": self.context.node["fingerprint"]}
        )
        context = replace(self.context, node=node, inputs=dict(zip(("a", "b"), args, strict=False)))
        return evaluate_temporal(context, temporal_parameters(rule, self.parameters))


def temporal_parameters(
    rule: dm.TemporalRule, parameters: dict[str, Any] | None = None
) -> TemporalParameters:
    return TemporalParameters(
        operation=rule.operation,
        duration_s=number_slot(rule.duration_s, parameters or {}),
        accepts_held=rule.accepts_held,
    )


def compile_machine(
    definition: dm.DeclarativeDefinition,
    *,
    evaluation: Evaluation | None = None,
    parameters: dict[str, Any] | None = None,
) -> Machine:
    machine = definition.machine
    assert machine is not None

    def guard(rule: dm.Rule) -> Guard:
        def evaluate(inputs: dict[str, FieldValue], now: datetime) -> bool | None:
            try:
                value = (
                    evaluation or Evaluation(definition, inputs, now, parameters=parameters)
                ).evaluate(rule)
            except (ArithmeticError, LookupError, TypeError, ValueError) as error:
                # Only local evaluator failures cross this boundary. Infrastructure
                # errors and the command transaction/commit stay outside it.
                raise GuardEvaluationError("evaluation_error") from error
            if evaluation:
                evaluation.machine_evidence(value)
                evaluation.trace.append(
                    {
                        "op": "guard",
                        "decision": value.value if value.usable(now) else None,
                    }
                )
            return value.value is True if value.usable(now) else None

        return evaluate

    def argument_guard(expected: dict[str, Any]) -> Callable[[dict[str, Any]], bool]:
        return lambda args: all(args.get(name) == value for name, value in expected.items())

    def data_confirmation(rule: dm.Rule) -> Guard:
        def confirmed(inputs: dict[str, FieldValue], now: datetime) -> bool:
            value = (
                evaluation or Evaluation(definition, inputs, now, parameters=parameters)
            ).evaluate(rule)
            if evaluation:
                evaluation.machine_evidence(value)
            return (
                value.status == "valid"
                and value.quality == "healthy"
                and value.value is True
                and bool(value.evidence)
            )

        return confirmed

    return Machine(
        Definition(
            states=machine.states,
            initial=machine.initial,
            commands=tuple(machine.commands),
            deadline_seconds=number_slot(
                machine.deadline_s, evaluation.parameters if evaluation else parameters or {}
            )
            if machine.deadline_s is not None
            else None,
            sessions=machine.sessions,
            allow_manual_restart=machine.allow_manual_restart,
            deadline_by_state={
                name: number_slot(
                    item.duration, evaluation.parameters if evaluation else parameters or {}
                )
                for name, item in machine.deadline_by_state.items()
            },
            absolute_transition=machine.absolute_transition.model_dump()
            if machine.absolute_transition
            else None,
            deadline_v2=machine.unknown_deadline is not None,
            required_inputs=tuple(
                item.name for item in definition.inputs if item.field_schema.required
            ),
            transitions=tuple(
                Transition(
                    source=t.source,
                    target=t.target,
                    event=t.event,
                    guard=guard(t.guard),
                    reason=t.reason,
                    new_episode=t.new_episode,
                    ends_episode=t.ends_episode,
                    argument_guard=argument_guard(t.argument_equals) if t.argument_equals else None,
                    data_confirmation=data_confirmation(t.guard),
                    deadline_action=t.deadline_action,
                )
                for t in machine.transitions
            ),
        )
    )


def command_model(name: str, command: dm.CommandDefinition) -> type[BaseModel]:
    class Arguments(EmptyParameters):
        @model_validator(mode="after")
        def validate_values(self) -> Arguments:
            for key, value in self.model_dump().items():
                if key in self.model_fields_set and not command.arguments[key].accepts(value):
                    raise ValueError("command_argument_type_mismatch")
            return self

    fields: dict[str, Any] = {}
    for key, schema in command.arguments.items():
        annotation: Any = {
            "bool": bool,
            "number": float,
            "text": str,
            "timestamp": str,
            "object": dict[str, Any] | list[Any],
            "enum": str,
        }[schema.type]
        if schema.value_catalog:
            annotation = Literal[tuple(schema.value_catalog)]
        fields[key] = (
            annotation,
            Field(
                default=... if schema.required else None,
                description=f"Unit: {schema.unit or 'none'}",
                json_schema_extra={"format": "date-time"} if schema.type == "timestamp" else None,
            ),
        )
    return create_model(name, __base__=Arguments, **fields)


def compile_definition(definition: dm.DeclarativeDefinition) -> ContractType:
    check_definition(definition)
    temporal_rules = [r for r in all_rules(definition) if isinstance(r, dm.TemporalRule)]

    def evaluate(ctx: Context) -> dict[str, FieldValue]:
        if any(isinstance(r, dm.TimeReachedRule) for r in all_rules(definition)):
            ctx.node["clock_due"] = []
        for name in definition.refresh_inputs:
            requested = ctx.node.setdefault("refresh", {})
            if ctx.input(name).usable(ctx.now):
                requested[name] = False
            elif ctx.origin == "input" and not requested.get(name):
                ctx.request_refresh(name)
                requested[name] = True
        evaluation = Evaluation(definition, ctx.inputs, ctx.now, ctx)
        values = {name: evaluation.evaluate(rule) for name, rule in definition.outputs.items()}
        from .value_origin import ValueOrigin, contributions

        for name, origin in definition.output_origins.items():
            field = values[name]
            if field.usable(ctx.now):
                values[name] = field.model_copy(
                    update={
                        "value_origin": ValueOrigin(
                            **origin.model_dump(),
                            valid_from=field.valid_from,
                            valid_until=field.valid_until,
                            based_on=field.evidence[:64],
                        ),
                        "input_origins": contributions([field]),
                    }
                )
        if any(
            value.usable(ctx.now) and not definition.output_schema.fields[name].accepts(value.value)
            for name, value in values.items()
        ):
            raise ValueError("declarative_output_type_mismatch")
        trace = {
            "registry_revision": ctx.state.active_revision,
            "epoch_id": ctx.state.epoch_id,
            "steps": evaluation.trace[:1024],
            "truncated": len(evaluation.trace) > 1024,
            "cost": evaluation.cost,
        }
        previous = ctx.node.get("trace", {})
        if ctx.origin != "revision" or trace != {
            key: value for key, value in previous.items() if key not in {"at", "origin"}
        }:
            ctx.node["trace"] = {"at": ctx.now.isoformat(), "origin": ctx.origin, **trace}
        return values

    def next_due(
        parameters: dict[str, Any], node: dict[str, Any], now: datetime
    ) -> datetime | None:
        due = [
            next_temporal_due(
                temporal_parameters(rule, parameters).model_dump(),
                node.get("rules", {}).get(rule.id, {}),
                now,
            )
            for rule in temporal_rules
        ]
        due.append(next_machine_due(parameters, node, now))
        due.extend(datetime.fromisoformat(at) for at in node.get("clock_due", []))
        return min((at for at in due if at is not None and at > now), default=None)

    return ContractType(
        type_id=definition.type_id,
        type_version=definition.type_version,
        schema=definition.output_schema,
        producer="state_machine" if definition.machine else "resolver",
        parameters=parameter_model(definition) if definition.parameter_schema else EmptyParameters,
        inputs=tuple(
            InputDeclaration(name=i.name, required=i.field_schema.required)
            for i in definition.inputs
        ),
        restore=("rules", "machine") if definition.machine else ("rules",),
        evaluate=Producer(evaluate),
        next_due=next_due,
        machine_factory=(lambda values: compile_machine(definition, parameters=values))
        if definition.machine
        else None,
        commands={
            name: command_model(name, command)
            for name, command in definition.machine.commands.items()
        }
        if definition.machine
        else {},
        definition=definition.model_dump(mode="json"),
        refresh_inputs=definition.refresh_inputs,
        grace_outputs=tuple(definition.outputs)
        if any(r.operation == "grace" for r in temporal_rules)
        else (),
    )
