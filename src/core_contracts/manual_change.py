"""An observed HA value change, classified after a bounded attribution window."""

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from .ha_context import MANUAL_CHANGE_TYPE, ContextIndex, EngineProof, HAContext
from .model import Model
from .producer import Context, Producer
from .quality import FieldValue, ReasonCode, unknown
from .schema import ContractType, FieldSchema, InputDeclaration, Schema, TypeRegistry

ORIGINS = ("engine", "ha_automation", "ha_user", "unattributed")


class ManualChangeParameters(Model):
    attribution_wait_s: int = Field(
        default=5,
        strict=True,
        ge=1,
        le=60,
        title="Zuordnungsfrist",
        description="So viele Sekunden wartet Wuschel auf einen Automatikbeleg.",
        json_schema_extra={"unit": "s", "display": "plain"},
    )


class Change(Model):
    changed_at: datetime
    received_at: datetime
    ha_epoch: str
    context_id: str | None
    parent_id: str | None
    has_ha_user: bool
    evidence: str
    automatic: Literal["engine", "ha_automation"] | None = None
    producer_id: str | None = None
    automation_id: str | None = None
    matched_context_id: str | None = None


def remember(ctx: Context) -> None:
    event = ctx.event
    if (
        event is None
        or event.source_id != ctx.contract.inputs[0].ref
        or not event.ha_change_epoch
        or not event.ha_time_fired
        or event.observation_kind != "live_change"
    ):
        return
    context = event.ha_context or HAContext()
    attribution = event.attribution
    automatic: Literal["engine", "ha_automation"] | None = None
    if attribution and attribution.kind == "known_automatic":
        automatic = "ha_automation" if attribution.basis == "ha_run" else "engine"
    ctx.node["manual_change"] = Change(
        changed_at=event.ha_time_fired,
        received_at=event.received_at,
        ha_epoch=event.ha_change_epoch,
        context_id=context.id,
        parent_id=context.parent_id,
        has_ha_user=bool(context.user_id and context.parent_id is None),
        evidence=event.reference,
        automatic=automatic,
        producer_id=attribution.producer_id if attribution else None,
        automation_id=attribution.automation_id if attribution else None,
        matched_context_id=attribution.matched_context_id if attribution else None,
    ).model_dump(mode="json")


def apply_registration(
    node: dict[str, Any], index: ContextIndex, root: str, proof: EngineProof
) -> bool:
    raw = node.get("manual_change")
    if not raw:
        return False
    change = Change.model_validate(raw)
    if change.ha_epoch != index.epoch or change.changed_at > proof.registered_at:
        return False
    if change.context_id is None or change.context_id not in index.links:
        return False
    if proof.registered_at - change.changed_at > proof.expires_at - proof.registered_at:
        return False
    chain = index.chain(HAContext(id=change.context_id, parent_id=change.parent_id))
    if root not in chain or (
        change.automatic == "engine"
        and change.matched_context_id is not None
        and change.matched_context_id in chain
        and chain.index(change.matched_context_id) <= chain.index(root)
    ):
        return False
    node["manual_change"] = change.model_copy(
        update={
            "automatic": "engine",
            "producer_id": proof.producer_id,
            "automation_id": None,
            "matched_context_id": root,
        }
    ).model_dump(mode="json")
    return True


def due(parameters: dict[str, Any], node: dict[str, Any], now: datetime) -> datetime | None:
    if not (raw := node.get("manual_change")) or raw.get("automatic"):
        return None
    wait = ManualChangeParameters.model_validate(parameters).attribution_wait_s
    deadline = datetime.fromisoformat(raw["received_at"]) + timedelta(seconds=wait)
    return deadline if deadline > now else None


def fields(ctx: Context) -> dict[str, FieldValue]:
    now = ctx.now
    source = ctx.input("source")
    if ctx.origin in {"restore", "snapshot"} or source.status != "valid" or not source.usable(now):
        ctx.node.pop("manual_change", None)
    if source.status == "valid" and source.usable(now):
        remember(ctx)
    absent = unknown(ReasonCode.INPUT_ABSENT, "source", now)
    raw = ctx.node.get("manual_change")
    if not raw:
        unavailable = source if source.status in {"unknown", "unresolved"} else absent
        return dict.fromkeys(SCHEMA.fields, unavailable)
    change = Change.model_validate(raw)

    def field(value: Any) -> FieldValue:
        return FieldValue(
            status="valid" if value is not None else "not_applicable",
            value=value,
            quality=source.quality if value is not None else "healthy",
            positively_not_applicable=value is None,
            evidence=(change.evidence,),
            measured_at=change.changed_at,
            since_at=change.changed_at,
        )

    pending = due(ctx.contract.parameters, ctx.node, now)
    origin = change.automatic or ("ha_user" if change.has_ha_user else "unattributed")
    waiting = unknown(ReasonCode.ATTRIBUTION_PENDING, "source", now).model_copy(
        update={"evidence": (change.evidence,)}
    )
    return {
        "value": waiting if pending else field(change.automatic is None),
        "origin": waiting if pending else field(origin),
        "changed_at": field(change.changed_at),
        "producer_id": waiting if pending else field(change.producer_id),
        "automation_id": waiting if pending else field(change.automation_id),
    }


SCHEMA = Schema(
    fields={
        "value": FieldSchema(type="bool", required=True, physical_state=False),
        "origin": FieldSchema(
            type="enum", required=True, physical_state=False, value_catalog=ORIGINS
        ),
        "changed_at": FieldSchema(type="timestamp", required=True, physical_state=False),
        "producer_id": FieldSchema(type="text", required=False, physical_state=False),
        "automation_id": FieldSchema(type="text", required=False, physical_state=False),
    },
    available_projection=("value",),
    fixture=False,
)


def register(registry: TypeRegistry) -> None:
    registry.register(
        ContractType(
            type_id=MANUAL_CHANGE_TYPE,
            type_version=1,
            schema=SCHEMA,
            producer="resolver",
            parameters=ManualChangeParameters,
            inputs=(InputDeclaration(name="source", required=True),),
            restore=(),
            evaluate=Producer(fields),
            next_due=due,
        )
    )
