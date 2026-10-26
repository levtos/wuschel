"""Declarative, code-owned state machines; commands request guarded transitions."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from .model import Model
from .quality import FieldValue, ReasonCode, unknown
from .temporal import Deadline

Guard = Callable[[dict[str, FieldValue], datetime], bool | None]


@dataclass(frozen=True)
class Transition:
    source: str
    target: str
    event: str
    guard: Guard
    reason: str
    new_episode: bool = False
    ends_episode: bool = False
    argument_guard: Callable[[dict[str, Any]], bool] | None = None
    data_confirmation: Guard | None = None
    deadline_action: Literal["keep", "reset", "clear"] = "keep"
    reset_trigger: str | None = None
    entry_label: str | None = None


@dataclass(frozen=True)
class Definition:
    states: tuple[str, ...]
    initial: str
    transitions: tuple[Transition, ...]
    commands: tuple[str, ...]
    deadline_seconds: float | None = None
    sessions: bool = False
    allow_manual_restart: bool = False
    deadline_v2: bool = False
    deadline_by_state: dict[str, float] = field(default_factory=dict)
    absolute_transition: dict[str, Any] | None = None
    required_inputs: tuple[str, ...] = ()
    ordered: bool = False
    required_inputs_by_state: dict[str, tuple[str, ...]] = field(default_factory=dict)
    captures: dict[str, str] = field(default_factory=dict)
    absolute_value: Callable[[MachineState], tuple[FieldValue, str | None]] | None = None
    on_state: Callable[[MachineState], None] | None = None

    def __post_init__(self) -> None:
        if self.initial not in self.states or len(set(self.states)) != len(self.states):
            raise ValueError("invalid states")
        for transition in self.transitions:
            if transition.source not in self.states or transition.target not in self.states:
                raise ValueError("invalid transition")


class MachineState(Model):
    state: str
    since_at: datetime
    previous_state: str | None = None
    episode_id: str
    episode_started_at: datetime
    episode_ended_at: datetime | None = None
    session_id: str | None = None
    transition_reason: str
    trigger: str
    source: str
    evidence: tuple[str, ...] = ()
    registry_revision: int
    origin: Literal["automatic", "manual", "restore"]
    fingerprint: str
    deadline: Deadline | None = None
    manual_restart: bool = Field(default=False, exclude_if=lambda value: not value)
    manual_restart_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    deadline_generation: int = Field(default=0, exclude_if=lambda value: not value)
    absolute_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    reset_levels: dict[str, bool | None] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    state_format: Literal[1] | None = Field(default=None, exclude_if=lambda v: v is None)
    captures: dict[str, datetime] = Field(default_factory=dict, exclude_if=lambda v: not v)
    entry_label: str | None = Field(default=None, exclude_if=lambda v: v is None)
    last_action_origin: str | None = Field(default=None, exclude_if=lambda v: v is None)
    consumed_absolute: tuple[str, ...] = Field(
        default=(), max_length=128, exclude_if=lambda v: not v
    )
    absolute_key: str | None = Field(default=None, exclude_if=lambda v: v is None)
    episode_active: bool = Field(default=False, exclude_if=lambda v: not v)


class Machine:
    def __init__(self, definition: Definition) -> None:
        self.definition = definition

    def initial(self, now: datetime, revision: int, fingerprint: str) -> MachineState:
        episode = str(uuid4())
        return MachineState(
            state=self.definition.initial,
            since_at=now,
            episode_id=episode,
            episode_started_at=now,
            transition_reason="initial",
            trigger="first_start",
            source="runtime",
            registry_revision=revision,
            origin="automatic",
            fingerprint=fingerprint,
            session_id=str(uuid4()) if self.definition.sessions else None,
            deadline=self._deadline(episode, now, self.definition.initial),
            state_format=1 if self.definition.ordered else None,
            entry_label="initial" if self.definition.ordered else None,
        )

    def _deadline(self, episode: str, now: datetime, state: str = "") -> Deadline | None:
        seconds = (
            self.definition.deadline_by_state.get(state)
            if self.definition.deadline_v2
            else self.definition.deadline_seconds
        )
        return (
            Deadline(
                deadline_id=str(uuid4()), episode_id=episode, at=now + timedelta(seconds=seconds)
            )
            if seconds is not None
            else None
        )

    def prepare(
        self,
        state: MachineState,
        inputs: dict[str, FieldValue],
        now: datetime,
        *,
        baseline: bool = False,
    ) -> MachineState:
        if self.definition.on_state:
            self.definition.on_state(state)
        if not self.definition.deadline_v2:
            return state
        absolute = self.definition.absolute_transition
        at = None
        key = None
        value: FieldValue | None
        if absolute and state.state in absolute["states"]:
            if self.definition.absolute_value:
                value, key = self.definition.absolute_value(state)
            else:
                value = inputs.get(absolute.get("at_input") or "")
            if value and value.usable(now) and isinstance(value.value, str):
                parsed = datetime.fromisoformat(value.value.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    at = parsed
            if self.definition.ordered:
                key = key or (at.isoformat() if at else None)
                if key in state.consumed_absolute:
                    at = None
        levels = dict(state.reset_levels)
        if baseline:
            for index, rule in enumerate(self.definition.transitions):
                if rule.deadline_action == "reset" and rule.source == state.state:
                    levels[str(index)] = rule.guard(inputs, now)
        return state.model_copy(
            update={
                "absolute_at": at,
                "reset_levels": levels,
                "deadline_generation": state.deadline_generation + int(at != state.absolute_at),
                **({"absolute_key": key} if self.definition.ordered else {}),
            }
        )

    def due_event(self, state: MachineState, now: datetime) -> str | None:
        duration = state.deadline.at if state.deadline and state.deadline.due(now) else None
        absolute = state.absolute_at if state.absolute_at and state.absolute_at <= now else None
        if duration and absolute:
            policy = self.definition.absolute_transition or {}
            return (
                "absolute"
                if absolute < duration
                or absolute == duration
                and policy.get("priority") == "absolute"
                else "deadline"
            )
        return "absolute" if absolute else "deadline" if duration else None

    def step(
        self,
        state: MachineState,
        event: str,
        inputs: dict[str, FieldValue],
        now: datetime,
        revision: int,
        *,
        manual: bool = False,
        args: dict[str, Any] | None = None,
    ) -> tuple[MachineState, str]:
        if manual and event not in self.definition.commands:
            return state, "not_supported"
        if self.definition.on_state:
            self.definition.on_state(state)
        required = (
            self.definition.required_inputs_by_state.get(state.state, ())
            if (self.definition.ordered)
            else self.definition.required_inputs
        )
        if (
            (self.definition.deadline_v2 or self.definition.ordered)
            and not manual
            and any(name not in inputs or not inputs[name].usable(now) for name in required)
        ):
            return state, "guard_unknown"
        rules = self.definition.transitions
        if event == "absolute":
            absolute = self.definition.absolute_transition
            if (
                not absolute
                or state.state not in absolute["states"]
                or not state.absolute_at
                or state.absolute_at > now
            ):
                return state, "unchanged"
            rules = (
                Transition(
                    state.state,
                    absolute["target"],
                    "absolute",
                    lambda inputs, now: True,
                    "absolute_time_reached",
                    entry_label=absolute.get("entry_label"),
                ),
            )
        for index, rule in enumerate(rules):
            if rule.source != state.state or rule.event != event:
                continue
            if rule.argument_guard is not None and not rule.argument_guard(args or {}):
                if self.definition.ordered:
                    continue
                return state, "guard_rejected"
            allowed = rule.guard(inputs, now)
            if rule.deadline_action == "reset" and not (
                self.definition.ordered and (manual or rule.reset_trigger == "event")
            ):
                previous = state.reset_levels.get(str(index))
                state = state.model_copy(
                    update={"reset_levels": {**state.reset_levels, str(index): allowed}}
                )
                if allowed and (
                    previous is not False or not any(value.evidence for value in inputs.values())
                ):
                    return state, "unchanged"
            if allowed is None:
                return state, "guard_unknown"
            if not allowed:
                if self.definition.ordered:
                    continue
                if event == "deadline" and state.deadline:
                    return state.model_copy(
                        update={"deadline": state.deadline.model_copy(update={"fired": True})}
                    ), "unchanged"
                return state, "guard_rejected"
            episode = str(uuid4()) if rule.new_episode else state.episode_id
            replace_deadline = (
                rule.new_episode
                or self.definition.deadline_v2
                and (rule.target != state.state or rule.deadline_action == "reset")
            )
            deadline = (
                self._deadline(episode, now, rule.target) if replace_deadline else state.deadline
            )
            if rule.deadline_action == "clear":
                deadline = None
            if event == "deadline" and deadline and not replace_deadline:
                deadline = deadline.model_copy(update={"fired": True})
            additions: dict[str, Any] = {}
            if self.definition.ordered:
                captures = {} if rule.new_episode else dict(state.captures)
                if rule.target != state.state or rule.new_episode:
                    for slot, target in self.definition.captures.items():
                        if rule.target == target and slot not in captures:
                            captures[slot] = now
                consumed = () if rule.new_episode else state.consumed_absolute
                if event == "absolute" and state.absolute_key:
                    consumed = (*consumed, state.absolute_key)[-128:]
                additions = {
                    "state_format": 1,
                    "captures": captures,
                    "entry_label": (rule.entry_label or rule.reason)
                    if (rule.target != state.state or rule.new_episode)
                    else state.entry_label,
                    "last_action_origin": rule.entry_label or rule.reason,
                    "episode_active": False
                    if rule.ends_episode
                    else (True if rule.new_episode else state.episode_active),
                    "consumed_absolute": consumed,
                }
            return state.model_copy(
                update={
                    "state": rule.target,
                    "previous_state": state.state,
                    "since_at": state.since_at
                    if self.definition.deadline_v2
                    and rule.target == state.state
                    and not rule.new_episode
                    else now,
                    "episode_id": episode,
                    "episode_started_at": now if rule.new_episode else state.episode_started_at,
                    "episode_ended_at": now if rule.ends_episode else None,
                    "transition_reason": rule.reason,
                    "trigger": event,
                    "source": "command" if manual else "input",
                    "evidence": tuple(e for i in inputs.values() for e in i.evidence),
                    "registry_revision": revision,
                    "origin": "manual" if manual else "automatic",
                    "manual_restart": state.manual_restart
                    and not (
                        not manual
                        and event == "input"
                        and rule.data_confirmation is not None
                        and rule.data_confirmation(inputs, now) is True
                    ),
                    "deadline": deadline,
                    **additions,
                    **(
                        {
                            "deadline_generation": state.deadline_generation
                            + int(replace_deadline or rule.deadline_action == "clear"),
                            "absolute_at": None,
                        }
                        if self.definition.deadline_v2
                        else {}
                    ),
                }
            ), "accepted"
        if event == "deadline" and state.deadline:
            state = state.model_copy(
                update={"deadline": state.deadline.model_copy(update={"fired": True})}
            )
        return state, "guard_rejected" if manual else "unchanged"

    def validate_restore(
        self, state: MachineState, fingerprint: str, now: datetime
    ) -> FieldValue | None:
        if state.fingerprint != fingerprint or state.state not in self.definition.states:
            return unknown(ReasonCode.RESTORE_INCOMPATIBLE, "state_machine", now)
        if self.definition.ordered and state.state_format != 1:
            return unknown(ReasonCode.RESTORE_MISSING, "state_machine", now)
        if self.definition.ordered and (
            state.episode_started_at.tzinfo is None
            or state.since_at.tzinfo is None
            or set(state.captures) - self.definition.captures.keys()
            or any(
                stamp.tzinfo is None or not state.episode_started_at <= stamp <= now
                for stamp in state.captures.values()
            )
            or any(
                target == state.state and slot not in state.captures
                for slot, target in self.definition.captures.items()
            )
            or state.since_at > now
        ):
            return unknown(ReasonCode.RESTORE_MISSING, "state_machine", now)
        if state.deadline and state.deadline.episode_id != state.episode_id:
            return unknown(ReasonCode.RESTORE_MISSING, "deadline", now)
        return None
