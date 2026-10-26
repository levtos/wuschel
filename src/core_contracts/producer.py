"""Reusable, transaction-local producer orchestration; no fixture or domain dependencies.

Definitions and lifecycle hooks are pure code. Mutable context belongs to the staged
node or staged latch map, so a failed commit cannot advance a producer's memory.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, ValidationError

from .clock import Clock
from .evidence import Observation
from .model import Model
from .persistence import State
from .quality import FieldValue, Reason, ReasonCode, unknown
from .registry import Contract
from .resolver import Bucket
from .statemachine import Machine, MachineState
from .temporal import Temporal, TemporalState, age, delta


class TemporalParameters(Model):
    operation: Literal["stable_for", "dwell", "grace", "edge", "delta", "age"]
    duration_s: float = Field(gt=0)
    accepts_held: bool


@dataclass
class Context:
    clock: Clock
    latches: dict[str, Bucket]
    contract: Contract
    inputs: dict[str, FieldValue]
    node: dict[str, Any]
    state: State
    origin: str
    case: str
    override: MachineState | None
    reassessment: bool = False
    event: Observation | None = None

    @property
    def now(self) -> datetime:
        return self.clock.now_utc()

    def input(self, name: str) -> FieldValue:
        return self.inputs.get(name, unknown(ReasonCode.INPUT_ABSENT, name, self.now))

    def bucket(
        self, name: str, value: FieldValue, initial: bool, lower: float, upper: float
    ) -> FieldValue:
        key = (
            self.contract.contract_id if name == "value" else f"{self.contract.contract_id}:{name}"
        )
        return self.latches.setdefault(key, Bucket(initial, lower, upper)).evaluate(value, self.now)

    def request_refresh(self, input_name: str) -> None:
        """Stage a declared source refresh; Runtime dispatches only after commit."""
        requests = self.node.setdefault("refresh_requests", [])
        if input_name not in requests:
            requests.append(input_name)


Fields = Callable[[Context], dict[str, FieldValue]]
Hook = Callable[[Context], None]


@dataclass(frozen=True)
class Producer:
    """Code-defined fields with staged init/evaluate/dispose lifecycle.

    Hooks must not perform I/O or retain mutable state outside Context. init is run
    on first use in each process or after a semantic change. dispose is invoked by
    Runtime on removal, disable, semantic change and shutdown.
    """

    fields: Fields
    initialize: Hook | None = None
    finalize: Hook | None = None

    def init(self, ctx: Context) -> None:
        if self.initialize:
            self.initialize(ctx)

    def dispose(self, ctx: Context) -> None:
        if self.finalize:
            self.finalize(ctx)

    def __call__(
        self,
        clock: Clock,
        latches: dict[str, Bucket],
        contract: Contract,
        inputs: dict[str, FieldValue],
        node: dict[str, Any],
        state: State,
        origin: str,
        case: str,
        override: MachineState | None,
        *,
        reassessment: bool = False,
        event: Observation | None = None,
    ) -> tuple[dict[str, FieldValue], dict[str, Any], MachineState | None]:
        ctx = Context(
            clock,
            latches,
            contract,
            inputs,
            node,
            state,
            origin,
            case,
            override,
            reassessment,
            event,
        )
        if node.get("producer_epoch") != state.epoch_id:
            self.init(ctx)
            node["producer_epoch"] = state.epoch_id
        fields = self.fields(ctx)
        machine = MachineState.model_validate(node["machine"]) if "machine" in node else None
        return fields, node, machine


def temporal(ctx: Context, parameters: TemporalParameters, *, input_name: str = "a") -> FieldValue:
    """Restore and advance a temporal node without replaying events or renewing Grace."""
    now, node, p = ctx.now, ctx.node, parameters
    from .managed_catalog import dependencies

    missing_catalog_trace = (
        "temporal" in node
        and not node.get("catalog_dependencies")
        and any(e.startswith("catalog:") for value in ctx.inputs.values() for e in value.evidence)
    )
    if dependencies(node, list(ctx.inputs.values())) or missing_catalog_trace:
        for key in ("temporal", "restore_pending", "age_input", "age_value", "edge_active"):
            node.pop(key, None)
    a, b = ctx.input(input_name), ctx.input("b")
    previous = (
        TemporalState.model_validate(node["temporal"])
        if "temporal" in node
        else TemporalState(fingerprint=node["fingerprint"])
    )
    operation = Temporal(previous, p.accepts_held)
    if "restore_pending" in node and all(item.usable(now) for item in ctx.inputs.values()):
        pending = TemporalState.model_validate(node.pop("restore_pending"))
        operation.restore(
            pending, node["fingerprint"], now, decisive_inputs=list(ctx.inputs.values())
        )
    if ctx.origin == "restore":
        operation.restore(
            previous, node["fingerprint"], now, decisive_inputs=list(ctx.inputs.values())
        )
    if p.operation in {"stable_for", "dwell"}:
        result = operation.stable_for(a, p.duration_s, now)
    elif p.operation == "grace":
        result = operation.grace(a, p.duration_s, now, trigger=ctx.origin == "input")
    elif p.operation == "edge":
        result = operation.edge(a, now, allow_edge=ctx.origin == "input")
        node["edge_active"] = result.usable(now) and result.value is True
    elif p.operation == "delta":
        result = delta(a, b, now)
    else:
        result = age(a, now)
        # Sample only when decisive evidence changes, including held expiry.
        if result.usable(now):
            age_input = a.model_dump(mode="json")
            if node.get("age_input") == age_input:
                result = result.model_copy(update={"value": node["age_value"]})
            else:
                node["age_input"], node["age_value"] = age_input, result.value
        else:
            node.pop("age_input", None)
            node.pop("age_value", None)
    node["temporal"] = operation.state.model_dump(mode="json")
    return result


def state_machine(
    ctx: Context, machine: Machine, *, reinitialize: Callable[[Context], bool]
) -> FieldValue:
    """Generic CP-15 restore, explicit type-owned re-init rule and deadline dispatch."""
    now, node = ctx.now, ctx.node
    from .managed_catalog import dependencies, suspend_machine

    missing_catalog_trace = (
        "machine" in node
        and not node.get("catalog_dependencies")
        and any(e.startswith("catalog:") for value in ctx.inputs.values() for e in value.evidence)
    )
    if dependencies(node, list(ctx.inputs.values())) or missing_catalog_trace:
        suspend_machine(node)
    current: MachineState | None = ctx.override
    if "catalog_machine_pending" in node:
        if current is None:
            if ctx.origin != "input" or not reinitialize(ctx):
                return unknown(ReasonCode.RESTORE_INCOMPATIBLE, ctx.contract.contract_id, now)
            current = machine.initial(
                now, ctx.state.active_revision, node["fingerprint"]
            ).model_copy(
                update={
                    "transition_reason": "reinitialize_when",
                    "trigger": "reinitialize_when",
                    "source": "evidence",
                    "evidence": tuple(node.get("machine_recovery", {}).get("evidence", ())),
                }
            )
        node.pop("catalog_machine_pending")
    if current is None and "machine" in node:
        try:
            current = MachineState.model_validate(node["machine"])
            invalid = machine.validate_restore(current, node["fingerprint"], now)
        except ValidationError:
            invalid = unknown(ReasonCode.RESTORE_MISSING, ctx.contract.contract_id, now)
        if invalid:
            if reinitialize(ctx):
                current = machine.initial(now, ctx.state.active_revision, node["fingerprint"])
                current = current.model_copy(
                    update={
                        "transition_reason": "reinitialize_when",
                        "trigger": "reinitialize_when",
                        "source": "evidence",
                        "evidence": tuple(node.get("machine_recovery", {}).get("evidence", ())),
                    }
                )
            else:
                node.pop("machine", None)
                return invalid
    elif current is None and ctx.case == "first":
        current = machine.initial(now, ctx.state.active_revision, node["fingerprint"])
    elif current is None and reinitialize(ctx):
        current = machine.initial(now, ctx.state.active_revision, node["fingerprint"])
        current = current.model_copy(
            update={
                "transition_reason": "reinitialize_when",
                "trigger": "reinitialize_when",
                "source": "evidence",
                "evidence": tuple(node.get("machine_recovery", {}).get("evidence", ())),
            }
        )
    elif current is None:
        code = (
            ReasonCode.RESTORE_INCOMPATIBLE
            if ctx.case == "incompatible"
            else ReasonCode.RESTORE_MISSING
        )
        return unknown(code, ctx.contract.contract_id, now)
    assert current is not None
    if (
        current.manual_restart
        and ctx.override is None
        and ctx.origin == "input"
        and reinitialize(ctx)
        and node.get("machine_recovery", {}).get("quality") == "healthy"
    ):
        # A genuine declared recovery starts an automatic episode; receipt/ticks,
        # constants and restore never confirm a manually asserted state.
        current = machine.initial(now, ctx.state.active_revision, node["fingerprint"]).model_copy(
            update={
                "transition_reason": "reinitialize_when",
                "trigger": "reinitialize_when",
                "source": "evidence",
                "evidence": tuple(node.get("machine_recovery", {}).get("evidence", ())),
            }
        )
    if machine.definition.ordered:
        current = machine.prepare(
            current, ctx.inputs, now, baseline=ctx.origin in {"restore", "snapshot", "revision"}
        )
        if ctx.override is None:
            if ctx.origin == "input":
                current, _ = machine.step(
                    current, "input", ctx.inputs, now, ctx.state.active_revision
                )
                current = machine.prepare(current, ctx.inputs, now)
            # Static validation forbids backward local dependencies; time steps are bounded.
            for _ in range(2):
                event = machine.due_event(current, now)
                if event is None:
                    break
                current, outcome = machine.step(
                    current, event, ctx.inputs, now, ctx.state.active_revision
                )
                if outcome == "guard_unknown":
                    node["machine"] = current.model_dump(mode="json")
                    return unknown(ReasonCode.DEADLINE_OVERDUE, ctx.contract.contract_id, now)
                current = machine.prepare(current, ctx.inputs, now)
                if outcome != "accepted":
                    break
        if ctx.origin == "restore":
            current = current.model_copy(update={"origin": "restore"})
    elif ctx.override is None:
        current = machine.prepare(
            current, ctx.inputs, now, baseline=ctx.origin in {"restore", "snapshot", "revision"}
        )
        if machine.definition.deadline_v2 and ctx.origin == "input":
            current, _ = machine.step(current, "input", ctx.inputs, now, ctx.state.active_revision)
            current = machine.prepare(current, ctx.inputs, now)
        event = machine.due_event(current, now) or "input"
        if (ctx.origin == "input" and not machine.definition.deadline_v2) or event in {
            "deadline",
            "absolute",
        }:
            current, outcome = machine.step(
                current, event, ctx.inputs, now, ctx.state.active_revision
            )
            if event in {"deadline", "absolute"} and outcome == "guard_unknown":
                node["machine"] = current.model_dump(mode="json")
                return unknown(ReasonCode.DEADLINE_OVERDUE, ctx.contract.contract_id, now)
            if machine.definition.deadline_v2 and outcome != "accepted":
                # A rejected duration guard may leave an absolute transition due at
                # the same instant; process that candidate within this transaction.
                remaining = machine.due_event(current, now)
                if remaining and remaining != event:
                    current, outcome = machine.step(
                        current, remaining, ctx.inputs, now, ctx.state.active_revision
                    )
                    if outcome == "guard_unknown":
                        node["machine"] = current.model_dump(mode="json")
                        return unknown(ReasonCode.DEADLINE_OVERDUE, ctx.contract.contract_id, now)
        if ctx.origin == "restore":
            current = current.model_copy(update={"origin": "restore"})
    if "catalog_machine_pending" in node:
        return unknown(ReasonCode.RESTORE_INCOMPATIBLE, ctx.contract.contract_id, now)
    node["machine"] = current.model_dump(mode="json")
    return FieldValue(
        status="valid",
        value=current.state,
        quality="degraded" if current.manual_restart else "healthy",
        since_at=current.since_at,
        evidence=current.evidence,
        reasons=(
            Reason(
                code=ReasonCode.MANUAL_RESTART,
                input=ctx.contract.contract_id,
                since=current.manual_restart_at or current.episode_started_at,
            ),
        )
        if current.manual_restart
        else (),
    )


def next_temporal_due(
    parameters: dict[str, Any], node: dict[str, Any], now: datetime
) -> datetime | None:
    p = TemporalParameters.model_validate(parameters)
    context = node.get("temporal", {})
    if p.operation == "edge" and node.get("edge_active"):
        return now + timedelta(seconds=0.5)
    if p.operation in {"stable_for", "dwell"} and context.get("since_at"):
        return datetime.fromisoformat(context["since_at"]) + timedelta(seconds=p.duration_s)
    if p.operation == "grace" and context.get("grace_until"):
        return datetime.fromisoformat(context["grace_until"])
    return None


def next_machine_due(
    parameters: dict[str, Any], node: dict[str, Any], now: datetime
) -> datetime | None:
    state = node.get("machine", {})
    deadline = state.get("deadline")
    due = [datetime.fromisoformat(deadline["at"])] if deadline and not deadline["fired"] else []
    if state.get("absolute_at"):
        due.append(datetime.fromisoformat(state["absolute_at"]))
    return min(due, default=None)
