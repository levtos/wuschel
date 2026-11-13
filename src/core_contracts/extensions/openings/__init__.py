"""Opening facts; actuation and consumer safety choices remain outside Wuschel."""

from datetime import datetime, timedelta
from typing import Any

from pydantic import Field

from core_contracts.fusion import Fusion
from core_contracts.model import Model
from core_contracts.producer import Context, Producer
from core_contracts.quality import FieldValue, Reason, ReasonCode, derive, unknown
from core_contracts.schema import ContractType, FieldSchema, InputDeclaration, Schema, TypeRegistry
from core_contracts.temporal import Temporal, TemporalState

OPENING_TYPES = {"opening.dual_contact", "opening.single_contact"}
STATES = ("closed", "tilted", "open")


class OpeningParameters(Model):
    grace_s: float = Field(
        strict=True,
        gt=0,
        le=31_536_000,
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
    now, node = ctx.now, ctx.node
    if ctx.contract.type_id == "opening.dual_contact":
        value = FUSION.evaluate(
            "opening_dual_contact_v1", [ctx.input("open"), ctx.input("tilt")], now
        )
    else:
        contact = ctx.input("contact")
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
        value = temporal.grace(
            value, float(ctx.contract.parameters["grace_s"]), now, trigger=ctx.origin == "input"
        )
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
                    "grace_until": min(now, loss_at)
                    + timedelta(seconds=float(ctx.contract.parameters["grace_s"])),
                    "grace_armed": False,
                }
            )
        missing = value
        value = temporal.grace(value, float(ctx.contract.parameters["grace_s"]), now, trigger=False)
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
    return {
        "value": value.model_copy(update={"since_at": datetime.fromisoformat(node["since_at"])})
    }


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


def register(registry: TypeRegistry) -> None:
    for type_id in sorted(OPENING_TYPES):
        names = ("open", "tilt") if type_id == "opening.dual_contact" else ("contact",)
        registry.register(
            ContractType(
                type_id=type_id,
                type_version=1,
                producer="fusion",
                parameters=OpeningParameters,
                schema=Schema(
                    fields={
                        "value": FieldSchema(
                            type="enum", required=True, physical_state=True, value_catalog=STATES
                        )
                    },
                    available_projection=("value",),
                    grace_capable=True,
                    grace_parameter="grace_s",
                    fixture=False,
                ),
                inputs=tuple(InputDeclaration(name=n, required=True) for n in names),
                restore=("temporal",),
                evaluate=Producer(opening),
                next_due=opening_due,
            )
        )
    registry.register(
        ContractType(
            type_id="opening_zone.facts",
            type_version=1,
            producer="resolver",
            parameters=ZoneParameters,
            schema=Schema(
                fields={
                    n: FieldSchema(type="bool", required=True, physical_state=False)
                    for n in ("any_open", "any_tilted", "unclear", "any_held", "all_closed")
                },
                available_projection=("unclear",),
                fixture=False,
            ),
            inputs=tuple(
                InputDeclaration(name=f"member_{i}", required=i == 1) for i in range(1, 5)
            ),
            restore=("facts",),
            evaluate=Producer(zone),
            next_due=zone_due,
        )
    )
