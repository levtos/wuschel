"""Opening facts; actuation and consumer safety choices remain outside Wuschel."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from math import nextafter
from typing import TYPE_CHECKING, Any

from pydantic import Field

from core_contracts import declarative_models as dm
from core_contracts import slots
from core_contracts.fusion import Fusion
from core_contracts.instance_parameters import number_slot
from core_contracts.model import Model
from core_contracts.producer import Context, Producer
from core_contracts.quality import FieldValue, Reason, ReasonCode, derive, unknown
from core_contracts.schema import ContractType, FieldSchema, InputDeclaration, Schema, TypeRegistry
from core_contracts.temporal import Temporal, TemporalState

if TYPE_CHECKING:
    from core_contracts.declarative import Evaluation

OPENING_TYPES = {"opening.dual_contact", "opening.single_contact"}
STATES = ("closed", "tilted", "open")
ZONE_FIELDS = ("any_open", "any_tilted", "unclear", "any_held", "all_closed")
MAX_GRACE = 31_536_000


class OpeningParameters(Model):
    grace_s: float = Field(
        strict=True,
        gt=0,
        le=MAX_GRACE,
        allow_inf_nan=False,
        title="grace_s",
    )


class ZoneParameters(Model):
    pass


def fresh(value: FieldValue, now: datetime) -> bool:
    return value.status == "valid" and value.usable(now) and value.freshness == "fresh"


def dual_contact(candidates: list[FieldValue], now: datetime) -> FieldValue:
    opened, tilted = candidates
    a, b = fresh(opened, now), fresh(tilted, now)
    if any(fresh(v, now) and type(v.value) is not bool for v in candidates):
        return unknown(ReasonCode.INVALID_VALUE, "contacts", now)
    if a and b:
        if opened.value and not tilted.value:
            return unknown(ReasonCode.CONFLICT, "contacts", now).model_copy(
                update={"evidence": tuple(e for v in candidates for e in v.evidence)}
            )
        result = derive(
            "open" if opened.value else "tilted" if tilted.value else "closed", candidates, now
        )
    elif a and opened.value is True:
        result = derive("open", [opened], now).model_copy(
            update={
                "quality": "degraded",
                "reasons": (Reason(code=ReasonCode.PARTIAL_EVIDENCE, input="tilt", since=now),)
                + tilted.reasons,
                "evidence": tuple(dict.fromkeys(opened.evidence + tilted.evidence)),
            }
        )
    else:
        result = unknown(ReasonCode.INPUT_UNKNOWN, "contacts", now).model_copy(
            update={
                "reasons": tuple(r for v in candidates for r in v.reasons)
                or (Reason(code=ReasonCode.PARTIAL_EVIDENCE, input="contacts", since=now),),
                "evidence": tuple(dict.fromkeys(e for v in candidates for e in v.evidence)),
            }
        )
    return result.model_copy(
        update={"freshness": "fresh" if result.status == "valid" else "invalid"}
    )


FUSION = Fusion()
FUSION.register("opening_dual_contact_v1", dual_contact)


def opening(ctx: Context) -> dict[str, FieldValue]:
    names = ("open", "tilt") if ctx.contract.type_id == "opening.dual_contact" else ("contact",)
    return {
        "value": opening_value(
            ctx, [ctx.input(name) for name in names], float(ctx.contract.parameters["grace_s"])
        )
    }


def opening_value(ctx: Context, contacts: list[FieldValue], grace_s: float) -> FieldValue:
    now, node = ctx.now, ctx.node
    if len(contacts) == 2:
        value = FUSION.evaluate("opening_dual_contact_v1", contacts, now)
    else:
        contact = contacts[0]
        value = (
            derive("open" if contact.value else "closed", [contact], now).model_copy(
                update={"freshness": "fresh"}
            )
            if fresh(contact, now) and type(contact.value) is bool
            else unknown(ReasonCode.INVALID_VALUE, "contact", now)
            if fresh(contact, now)
            else contact
        )
    previous = TemporalState.model_validate(
        node.get("temporal", {"fingerprint": node["fingerprint"]})
    )
    temporal = Temporal(previous)
    if ctx.origin == "restore":
        temporal.restore(previous, node["fingerprint"], now, decisive_inputs=[])
    conflict = any(r.code == ReasonCode.CONFLICT for r in value.reasons)
    if conflict:
        temporal.state = TemporalState(fingerprint=node["fingerprint"])
    elif value.status == "valid":
        value = temporal.grace(value, grace_s, now, trigger=ctx.origin == "input")
    else:
        if ctx.reassessment and temporal.state.grace_until is None:
            temporal.state = temporal.state.model_copy(update={"grace_armed": False})
        if (
            temporal.state.grace_until is None
            and not ctx.reassessment
            and temporal.state.grace_armed
            and temporal.state.last_valid
        ):
            loss_at = min(
                (r.since for v in ctx.inputs.values() if not fresh(v, now) for r in v.reasons),
                default=now,
            )
            temporal.state = temporal.state.model_copy(
                update={
                    "grace_until": min(now, loss_at) + timedelta(seconds=grace_s),
                    "grace_armed": False,
                }
            )
        missing = value
        value = temporal.grace(value, grace_s, now, trigger=False)
        if value.status == "held":
            value = value.model_copy(
                update={
                    "freshness": "invalid",
                    "reasons": value.reasons + missing.reasons,
                    "evidence": tuple(dict.fromkeys(value.evidence + missing.evidence)),
                }
            )
    node["temporal"] = temporal.state.model_dump(mode="json")
    identity = (value.status, value.value)
    if ctx.origin == "restore" or tuple(node.get("identity", ())) != identity:
        node["since_at"] = now.isoformat()
    node["identity"] = list(identity)
    return value.model_copy(update={"since_at": datetime.fromisoformat(node["since_at"])})


def opening_due(parameters: dict[str, Any], node: dict[str, Any], now: datetime) -> datetime | None:
    stamp = node.get("temporal", {}).get("grace_until")
    return datetime.fromisoformat(stamp) if stamp else None


def zone(ctx: Context) -> dict[str, FieldValue]:
    now = ctx.now
    members = list(ctx.inputs.items())
    usable = [(name, v) for name, v in members if v.usable(now) and v.value in STATES]
    held = [(name, v) for name, v in usable if v.status == "held"]
    unclear = [
        (name, v)
        for name, v in members
        if not v.usable(now)
        or v.value not in STATES
        or any(r.code == ReasonCode.CONFLICT for r in v.reasons)
    ]
    predicates = {
        "any_open": any(v.value == "open" for _, v in usable),
        "any_tilted": any(v.value == "tilted" for _, v in usable),
        "unclear": bool(unclear),
        "any_held": bool(held),
        "all_closed": bool(members)
        and all(
            fresh(v, now) and v.value == "closed" and v.quality == "healthy" for _, v in members
        ),
    }
    contexts = ctx.node.setdefault("facts", {})
    references = {entry.name: entry.ref for entry in ctx.contract.inputs}
    result = {}
    for name, truth in predicates.items():
        old = contexts.get(name, {})
        since = (
            old.get("since_at") if old.get("value") is truth and ctx.origin != "restore" else None
        )
        contexts[name] = {"value": truth, "since_at": since or now.isoformat()}
        reasons = tuple(
            r.model_copy(update={"input": references[member], "source_id": r.source_id or r.input})
            for member, v in members
            for r in v.reasons
        )
        result[name] = FieldValue(
            status="valid",
            value=truth,
            quality="healthy",
            freshness="fresh",
            since_at=datetime.fromisoformat(contexts[name]["since_at"]),
            reasons=reasons,
            evidence=tuple(dict.fromkeys(e for _, v in members for e in v.evidence)),
        )
    ctx.node["held_deadlines"] = [v.held_until.isoformat() for _, v in held if v.held_until]
    return result


def zone_due(parameters: dict[str, Any], node: dict[str, Any], now: datetime) -> datetime | None:
    return min((datetime.fromisoformat(s) for s in node.get("held_deadlines", [])), default=None)


def contact_schema() -> FieldSchema:
    return FieldSchema(type="enum", required=True, physical_state=True, value_catalog=STATES)


def check_contact(
    rule: dm.OpeningContactRule,
    definition: dm.DeclarativeDefinition,
    inputs: dict[str, FieldSchema],
    args: list[FieldSchema],
    require: Callable[[FieldSchema, FieldSchema], FieldSchema],
    scalar: Callable[[str], FieldSchema],
) -> FieldSchema:
    for arg in args[: len(rule.args)]:
        require(arg, scalar("bool"))
    return contact_schema()


def check_zone(
    rule: dm.OpeningZoneRule,
    definition: dm.DeclarativeDefinition,
    inputs: dict[str, FieldSchema],
    args: list[FieldSchema],
    require: Callable[[FieldSchema, FieldSchema], FieldSchema],
    scalar: Callable[[str], FieldSchema],
) -> FieldSchema:
    for arg in args:
        require(arg, contact_schema())
    return scalar("bool")


def contact_children(rule: dm.OpeningContactRule) -> list[tuple[str, dm.Rule]]:
    children: list[tuple[str, dm.Rule]] = [
        (f"args/{index}", arg) for index, arg in enumerate(rule.args)
    ]
    if isinstance(rule.duration_s, dm.ParameterRule):
        children.append(("duration_s", rule.duration_s))
    return children


def zone_children(rule: dm.OpeningZoneRule) -> list[tuple[str, dm.Rule]]:
    return [(f"args/{index}", arg) for index, arg in enumerate(rule.args)]


def copy_state(ctx: Context, key: str) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = ctx.node.setdefault("rules", {})
    return nodes.setdefault(key, {"fingerprint": ctx.node["fingerprint"]})


def copy_due(ctx: Context, due: datetime | None) -> None:
    if due and due > ctx.now:
        ctx.node.setdefault("clock_due", []).append(due.isoformat())


def evaluate_contact(evaluation: Evaluation, rule: dm.OpeningContactRule) -> FieldValue:
    ctx = evaluation.context
    assert ctx is not None
    node = copy_state(ctx, rule.id)
    inputs = {arg.name: ctx.input(arg.name) for arg in rule.args}
    value = opening_value(
        replace(ctx, node=node, inputs=inputs),
        list(inputs.values()),
        number_slot(rule.duration_s, evaluation.parameters),
    )
    copy_due(ctx, opening_due({}, node, ctx.now))
    return value


def evaluate_zone(evaluation: Evaluation, rule: dm.OpeningZoneRule) -> FieldValue:
    ctx = evaluation.context
    assert ctx is not None
    names = tuple(arg.name for arg in rule.args)
    key = "opening_zone:" + ",".join(names)
    node = copy_state(ctx, key)
    if key + ":unclear" not in evaluation.memo:
        inputs = {name: ctx.inputs[name] for name in names if name in ctx.inputs}
        for field, value in zone(replace(ctx, node=node, inputs=inputs)).items():
            evaluation.memo[key + ":" + field] = value
        for at in node["held_deadlines"]:
            copy_due(ctx, datetime.fromisoformat(at))
    return evaluation.memo[key + ":" + rule.field]


slots.RULES[dm.OpeningContactRule] = slots.Rule(
    check=check_contact, children=contact_children, evaluate=evaluate_contact, due=copy_due
)
slots.RULES[dm.OpeningZoneRule] = slots.Rule(
    check=check_zone, children=zone_children, evaluate=evaluate_zone, due=copy_due
)


def editable_copy(
    type_id: str, schema: Schema, inputs: tuple[InputDeclaration, ...]
) -> dict[str, Any]:
    is_zone = type_id == "opening_zone.facts"
    args = [dm.InputRule(op="input", name=input.name) for input in inputs]
    outputs = (
        {name: {"op": "opening_zone", "field": name, "args": args} for name in schema.fields}
        if is_zone
        else {
            "value": dm.OpeningContactRule(
                op="opening_contact",
                id="contact",
                args=tuple(args),
                duration_s=dm.ParameterRule(op="parameter", name="grace_s"),
            )
        }
    )
    return dm.DeclarativeDefinition.model_validate(
        {
            "type_id": type_id,
            "type_version": 1,
            "language_version": 2,
            "inputs": [
                {
                    "name": input.name,
                    "schema": (
                        contact_schema().model_copy(update={"required": input.required})
                        if is_zone
                        else FieldSchema(type="bool", required=True, physical_state=False)
                    ),
                }
                for input in inputs
            ],
            "schema": schema.model_copy(update={"grace_capable": False, "grace_parameter": None}),
            "outputs": outputs,
            "parameter_schema": {}
            if is_zone
            else {
                "grace_s": dm.ParameterDefinition.model_validate(
                    {
                        "schema": FieldSchema(
                            type="number", required=True, physical_state=False, unit="s"
                        ),
                        "minimum": nextafter(0, 1),
                        "maximum": MAX_GRACE,
                        "display": "duration",
                    }
                )
            },
        }
    ).model_dump(mode="json")


def register(registry: TypeRegistry) -> None:
    for type_id in sorted(OPENING_TYPES):
        names = ("open", "tilt") if type_id == "opening.dual_contact" else ("contact",)
        inputs = tuple(InputDeclaration(name=n, required=True) for n in names)
        schema = Schema(
            fields={"value": contact_schema()},
            available_projection=("value",),
            grace_capable=True,
            grace_parameter="grace_s",
            fixture=False,
        )
        registry.register(
            ContractType(
                type_id=type_id,
                type_version=1,
                producer="fusion",
                parameters=OpeningParameters,
                schema=schema,
                inputs=inputs,
                restore=("temporal",),
                evaluate=Producer(opening),
                next_due=opening_due,
                editable_copy=editable_copy(type_id, schema, inputs),
            )
        )
    inputs = tuple(InputDeclaration(name=f"member_{i}", required=i == 1) for i in range(1, 5))
    schema = Schema(
        fields={
            n: FieldSchema(type="bool", required=True, physical_state=False) for n in ZONE_FIELDS
        },
        available_projection=("unclear",),
        fixture=False,
    )
    registry.register(
        ContractType(
            type_id="opening_zone.facts",
            type_version=1,
            producer="resolver",
            parameters=ZoneParameters,
            schema=schema,
            inputs=inputs,
            restore=("facts",),
            evaluate=Producer(zone),
            next_due=zone_due,
            editable_copy=editable_copy("opening_zone.facts", schema, inputs),
        )
    )
