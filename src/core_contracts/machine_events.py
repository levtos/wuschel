"""Qualify the current transaction's original observation; never replay a snapshot."""

from typing import TYPE_CHECKING

from . import declarative_models as dm
from .model import checksum
from .quality import FieldValue, derive
from .statemachine import MachineState

if TYPE_CHECKING:
    from .producer import Context


def event_match(
    rule: dm.EventMatchRule,
    value: FieldValue,
    ctx: Context,
    state: MachineState | None,
) -> FieldValue:
    slots = ctx.node.setdefault("events", {})
    old = slots.get(rule.id)
    if not value.usable(ctx.now) or value.status != "valid":
        slots.pop(rule.id, None)
        return value
    baseline = {"digest": checksum(value.value), "epoch": ctx.state.epoch_id}
    slots[rule.id] = baseline
    event = ctx.event
    qualifies = bool(
        old is not None
        and old.get("epoch") == ctx.state.epoch_id
        and ctx.origin == "input"
        and event
        and event.observation_kind in {"live_change", "report"}
        and not event.mqtt_retained
        and not event.ha_restored
        and event.epoch_id == ctx.state.epoch_id
        and event.reference in value.evidence
        and event.measurement is not None
        and event.measurement <= ctx.now
    )
    if qualifies and rule.after_state_entry:
        assert event is not None
        qualifies = bool(state and event.measurement and event.measurement > state.since_at)
    changed = qualifies and old is not None and old.get("digest") != baseline["digest"]
    if rule.mode == "rising":
        changed = changed and value.value is True
    elif rule.mode == "falling":
        changed = changed and value.value is False
    # A downstream reset sees an opaque proof of this event, not a synthesized level pulse.
    result = derive(bool(changed), [value], ctx.now)
    if changed and event:
        result = result.model_copy(
            update={"evidence": (*result.evidence, "event:" + checksum(event.reference))}
        )
    return result
