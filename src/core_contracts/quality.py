"""Field-level quality invariants and decisive-evidence propagation."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field as PField
from pydantic import field_validator, model_validator

from .ha_context import Attribution
from .model import Model, utc
from .value_origin import ValueOrigin, contributions, interval_bounds


class ReasonCode(StrEnum):
    INPUT_UNAVAILABLE = "input_unavailable"
    INPUT_UNKNOWN = "input_unknown"
    INPUT_STALE = "input_stale"
    CALENDAR_COVERAGE = "calendar_coverage_missing"
    INPUT_NOT_YET_VALID = "input_not_yet_valid"
    VALUE_VALIDITY_GAP = "value_validity_gap"
    SENSOR_STUCK = "sensor_stuck_suspected"
    INPUT_RESTORED = "input_restored"
    INPUT_ABSENT = "input_absent"
    UNMAPPED_VALUE = "unmapped_value"
    INVALID_VALUE = "invalid_value"
    NO_MATCH = "no_match"
    CONFLICT = "conflict"
    PARTIAL_EVIDENCE = "partial_evidence"
    GRACE = "source_transition_grace"
    SELECTION_BLOCKED = "selection_blocked"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CONFIG_INCOMPLETE = "config_incomplete"
    EVALUATION_ERROR = "evaluation_error"
    RESTORE_MISSING = "restore_context_missing"
    RESTORE_INCOMPATIBLE = "restore_context_incompatible"
    RESTORE_STALE = "restore_context_stale"
    HISTORY_GAP = "history_gap"
    DEADLINE_OVERDUE = "deadline_overdue_unprocessed"
    GUARD_REJECTED = "guard_rejected"
    MANUAL_RESTART = "manual_restart"


class Reason(Model):
    code: ReasonCode
    input: str = PField(min_length=1)
    source_id: str | None = None
    since: datetime
    detail: str | None = None

    _utc = field_validator("since")(utc)


class ConfigurationHint(Model):
    code: str = PField(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = PField(default="", max_length=256)


class ConfigurationStatus(Model):
    complete: bool = True
    hints: tuple[ConfigurationHint, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.complete == bool(self.hints):
            raise ValueError("configuration_hint_required")
        return self


class FieldValue(Model):
    status: Literal["valid", "held", "unknown", "not_applicable", "unresolved"]
    value: Any = None
    quality: Literal["healthy", "degraded"]
    held_until: datetime | None = None
    since_at: datetime | None = None
    freshness: str | None = None
    reasons: tuple[Reason, ...] = ()
    evidence: tuple[str, ...] = ()
    measured_at: datetime | None = None
    attribution: Attribution | None = PField(default=None, exclude_if=lambda value: value is None)
    value_origin: ValueOrigin | None = PField(default=None, exclude_if=lambda value: value is None)
    input_origins: tuple[ValueOrigin, ...] = PField(
        default=(), max_length=64, exclude_if=lambda value: not value
    )
    valid_from: datetime | None = PField(default=None, exclude_if=lambda value: value is None)
    valid_until: datetime | None = PField(default=None, exclude_if=lambda value: value is None)
    plausibility: Literal["unchecked", "plausible", "suspicious"] | None = PField(
        default=None, exclude_if=lambda value: value is None
    )
    grace_declared: bool = False
    positively_not_applicable: bool = False
    configuration: ConfigurationStatus = PField(
        default_factory=ConfigurationStatus, exclude_if=lambda value: value.complete
    )

    @field_validator("held_until", "since_at", "measured_at", "valid_from", "valid_until")
    @classmethod
    def times(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None

    @model_validator(mode="after")
    def invariant(self) -> Self:
        if self.status in {"valid", "held"} and self.value is None:
            raise ValueError("value_required")
        if self.status in {"unknown", "unresolved", "not_applicable"} and self.value is not None:
            raise ValueError("null_required")
        if self.status in {"unknown", "unresolved"}:
            if self.quality != "degraded" or not self.reasons:
                raise ValueError("reason_and_degraded_required")
        if self.status == "held":
            if (
                not self.held_until
                or not self.grace_declared
                or not self.reasons
                or self.quality != "healthy"
            ):
                raise ValueError("declared_grace_required")
        elif self.held_until is not None:
            raise ValueError("held_until_requires_held")
        if self.status == "not_applicable" and (
            not self.positively_not_applicable or self.quality != "healthy"
        ):
            raise ValueError("positive_determination_required")
        return self

    def usable(self, now: datetime) -> bool:
        return (
            (self.valid_from is None or self.valid_from <= now)
            and (self.valid_until is None or now < self.valid_until)
            and (
                self.status == "valid"
                or (self.status == "held" and self.held_until is not None and self.held_until > now)
            )
        )


def unknown(code: ReasonCode, input_id: str, now: datetime) -> FieldValue:
    return FieldValue(
        status="unknown",
        quality="degraded",
        reasons=(Reason(code=code, input=input_id, since=now),),
    )


def derive(value: Any, inputs: list[FieldValue], now: datetime) -> FieldValue:
    """Only decisive inputs belong here; their Grace cannot be renewed."""
    hints = tuple(dict.fromkeys(hint for item in inputs for hint in item.configuration.hints))
    configuration = ConfigurationStatus(complete=not hints, hints=hints)
    if any(not item.usable(now) for item in inputs):
        reasons = tuple(r for i in inputs for r in i.reasons)
        return FieldValue(
            status="unknown",
            quality="degraded",
            reasons=reasons or (Reason(code=ReasonCode.INPUT_UNKNOWN, input="inputs", since=now),),
            configuration=configuration,
        )
    ends = [i.held_until for i in inputs if i.status == "held" and i.held_until]
    times = [i.measured_at for i in inputs if i.measured_at]
    origins = contributions(inputs)
    begins = [item.valid_from for item in inputs if item.valid_from]
    finishes = [item.valid_until for item in inputs if item.valid_until]
    begin, finish = interval_bounds(origins)
    if begin:
        begins.append(begin)
    if finish:
        finishes.append(finish)
    return FieldValue(
        status="held" if ends else "valid",
        value=value,
        quality="healthy" if ends or all(i.quality == "healthy" for i in inputs) else "degraded",
        held_until=min(ends) if ends else None,
        grace_declared=bool(ends),
        reasons=tuple(r for i in inputs for r in i.reasons),
        evidence=tuple(dict.fromkeys(e for i in inputs for e in i.evidence)),
        measured_at=min(times) if times else None,
        attribution=inputs[0].attribution
        if inputs and all(item.attribution == inputs[0].attribution for item in inputs)
        else None,
        configuration=configuration,
        input_origins=origins,
        valid_from=max(begins, default=None),
        valid_until=min(finishes, default=None),
        plausibility="suspicious"
        if any(item.plausibility == "suspicious" for item in inputs)
        else None,
    )
