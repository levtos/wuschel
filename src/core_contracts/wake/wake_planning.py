# Ported from Levtos/benni-core-state @ c9408fa33f2914b1c5f7316738cb38b43add33b5.
# Preserve pure planning semantics; see docs/beta/bereich-4/A2.md.
"""Pure Wake-Planning-Shadow for Core State.

The module deliberately has no Home Assistant imports.  It owns the L1
decision boundary used by the #26 shadow and receives already selected,
localised inputs from the coordinator.  The old ``ha_wake_planner`` remains
the parallel reference; this module never writes to it and never actuates.

The implementation mirrors the proven automatic planner semantics:

* local civil date/time, weekday/weekend (Saturday included), holiday and
  holiday-represented vacation intervals;
* deterministic rule/date/period/cycle matching;
* calendar wake/skip markers and early-event conflict handling;
* the Core-State absolute local wake floor (06:00 by default);
* an inclusive, configurable wake window and a 30-day next-wake horizon.

Manual overrides and the Core-State Bio lifecycle remain outside this module.
The configured ``minimum_sleep_minutes`` value is carried into the separate
Phase-1 E/L/M/A contract; this module still never infers sleep.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

SHADOW_VERSION = "1.0.0"

WAKE_STATE_SCHEDULED = "scheduled"
WAKE_STATE_SKIPPED = "skipped"
WAKE_STATE_OVERRIDDEN = "overridden"
WAKE_STATE_HOLIDAY = "holiday"
WAKE_STATE_INACTIVE = "inactive"
WAKE_STATES: tuple[str, ...] = (
    WAKE_STATE_SCHEDULED,
    WAKE_STATE_SKIPPED,
    WAKE_STATE_OVERRIDDEN,
    WAKE_STATE_HOLIDAY,
    WAKE_STATE_INACTIVE,
)

RULE_ACTION_WAKE = "wake"
RULE_ACTION_SKIP = "skip"

HOLIDAY_SKIP = "skip"
HOLIDAY_WEEKEND_PROFILE = "weekend_profile"

CONFLICT_IGNORE = "ignore"
CONFLICT_WARN_ONLY = "warn_only"
CONFLICT_WAKE_EARLIER = "wake_earlier"
CONFLICT_BEHAVIORS: frozenset[str] = frozenset(
    {CONFLICT_IGNORE, CONFLICT_WARN_ONLY, CONFLICT_WAKE_EARLIER}
)

DEFAULT_WAKE_WINDOW_MINUTES = 5
DEFAULT_ROUTINE_DURATION_MINUTES = 60
DEFAULT_WAKE_FLOOR = time(6, 0)
DEFAULT_HORIZON_DAYS = 30
DEFAULT_CALENDAR_WAKE_PATTERN = r"(?:wake:\s*)?(?P<time>[0-2]?\d:[0-5]\d)"
DEFAULT_CALENDAR_SKIP_TITLES: tuple[str, ...] = ("no-wake", "schlaf aus")

COMPARISON_SAME = "same_decision"
COMPARISON_DIFFERENT = "different_decision"
COMPARISON_LEGACY_UNAVAILABLE = "legacy_unavailable"
COMPARISON_CORE_UNAVAILABLE = "core_state_unavailable"
COMPARISON_NOT_DECIDABLE = "not_decidable"


def parse_time(value: Any) -> time | None:
    """Parse a local ``HH:MM`` value without accepting invalid clock times."""

    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except TypeError, ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def parse_date(value: Any) -> date | None:
    """Parse an ISO date; malformed values are rejected, never guessed."""

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class WakeRule:
    """A pure copy of one proven automatic Wake-Planner rule."""

    id: str
    name: str
    priority: int = 100
    enabled: bool = True
    weekdays: frozenset[int] | None = None
    date_from: date | None = None
    date_to: date | None = None
    week_interval: int | None = None
    week_anchor: date | None = None
    specific_dates: tuple[date, ...] | None = None
    cycle_anchor: date | None = None
    cycle_length: int | None = None
    cycle_slot_start: int | None = None
    cycle_slot_length: int | None = None
    on_holiday: bool | None = None
    action: str = RULE_ACTION_WAKE
    wake_time: time | None = None


@dataclass(frozen=True, slots=True)
class RuleParseResult:
    rules: tuple[WakeRule, ...]
    invalid_reasons: tuple[str, ...] = ()


def default_rules() -> tuple[WakeRule, ...]:
    """Return the old planner's three automatic profile rules."""

    return (
        WakeRule(
            id="profile_weekday",
            name="Werktage",
            priority=100,
            weekdays=frozenset({0, 1, 2, 3, 4}),
            on_holiday=False,
            wake_time=time(7, 0),
        ),
        WakeRule(
            id="profile_weekend",
            name="Wochenende",
            priority=110,
            weekdays=frozenset({5, 6}),
            wake_time=time(9, 30),
        ),
        WakeRule(
            id="profile_holiday",
            name="Feiertage",
            priority=90,
            weekdays=frozenset({0, 1, 2, 3, 4}),
            on_holiday=True,
            wake_time=time(9, 30),
        ),
    )


def parse_rules(raw_rules: Any) -> RuleParseResult:
    """Parse stored planner rules, skipping invalid rules like the old code."""

    if raw_rules is None:
        return RuleParseResult((), ("rules_missing",))
    if not isinstance(raw_rules, (list, tuple)):
        return RuleParseResult((), ("rules_not_a_list",))

    rules: list[WakeRule] = []
    invalid: list[str] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, Mapping):
            invalid.append(f"rule_{index}_not_an_object")
            continue
        try:
            action = str(raw.get("action") or RULE_ACTION_WAKE)
            if action not in {RULE_ACTION_WAKE, RULE_ACTION_SKIP}:
                raise ValueError("invalid_action")
            wake = parse_time(raw.get("wake_time")) if raw.get("wake_time") else None
            if action == RULE_ACTION_WAKE and wake is None:
                raise ValueError("wake_time_missing_or_invalid")

            weekdays_raw = raw.get("weekdays")
            weekdays: frozenset[int] | None = None
            if weekdays_raw is not None:
                values = {int(value) for value in weekdays_raw if 0 <= int(value) <= 6}
                weekdays = frozenset(values) or None

            specific_raw = raw.get("specific_dates")
            specific: tuple[date, ...] | None = None
            if specific_raw is not None:
                parsed = tuple(
                    parsed_date
                    for parsed_date in (parse_date(item) for item in specific_raw)
                    if parsed_date is not None
                )
                specific = parsed or None

            on_holiday_raw = raw.get("on_holiday")
            if on_holiday_raw in (None, ""):
                on_holiday = None
            else:
                on_holiday = bool(on_holiday_raw)

            rules.append(
                WakeRule(
                    id=str(raw.get("id") or f"rule_{index}"),
                    name=str(raw.get("name") or "Rule"),
                    priority=int(raw.get("priority", 100)),
                    enabled=bool(raw.get("enabled", True)),
                    weekdays=weekdays,
                    date_from=parse_date(raw.get("date_from")),
                    date_to=parse_date(raw.get("date_to")),
                    week_interval=(
                        int(raw["week_interval"])
                        if raw.get("week_interval") not in (None, "")
                        else None
                    ),
                    week_anchor=parse_date(raw.get("week_anchor")),
                    specific_dates=specific,
                    cycle_anchor=parse_date(raw.get("cycle_anchor")),
                    cycle_length=(
                        int(raw["cycle_length"])
                        if raw.get("cycle_length") not in (None, "")
                        else None
                    ),
                    cycle_slot_start=(
                        int(raw["cycle_slot_start"])
                        if raw.get("cycle_slot_start") not in (None, "")
                        else None
                    ),
                    cycle_slot_length=(
                        int(raw["cycle_slot_length"])
                        if raw.get("cycle_slot_length") not in (None, "")
                        else None
                    ),
                    on_holiday=on_holiday,
                    action=action,
                    wake_time=wake,
                )
            )
        except KeyError, TypeError, ValueError:
            invalid.append(f"rule_{index}_invalid")

    return RuleParseResult(tuple(rules), tuple(invalid))


@dataclass(frozen=True, slots=True)
class WakeHoliday:
    """A holiday/day-off input; vacation has no separate public entity."""

    is_holiday: bool
    name: str | None = None
    is_vacation: bool = False
    source: str = "not_configured"
    quality: str = "fresh"

    def safe_name(self) -> str | None:
        """Return a bounded diagnostic name without copying calendar titles."""

        if not self.is_holiday:
            return None
        if self.source == "calendar_weekend" or self.name == "Weekend":
            return "Weekend"
        if self.source.startswith("manual") or self.name == "Manual holiday":
            return "Manual holiday"
        if self.source.startswith("calendar"):
            return "Holiday calendar"
        return "Holiday"


@dataclass(frozen=True, slots=True)
class WakeCalendarDecision:
    """Calendar markers after parsing; raw event text is not retained."""

    wake_time: time | None = None
    skip: bool = False
    early_event_time: time | None = None
    source: str = "not_configured"


@dataclass(frozen=True, slots=True)
class WakeInputStatus:
    """Source/freshness/quality metadata carried into diagnostics."""

    name: str
    source: str
    quality: str = "fresh"
    available: bool = True
    observed_at: datetime | None = None
    max_age_seconds: int | None = None
    optional: bool = False
    using_cache: bool = False
    reason: str = ""

    def effective_quality(self, now: datetime) -> str:
        if not self.available:
            return self.quality if self.quality != "fresh" else "unavailable"
        if self.quality in {"invalid", "missing", "unavailable", "stale"}:
            return self.quality
        if self.observed_at is None or self.max_age_seconds is None:
            return self.quality
        if _age_seconds(now, self.observed_at) > self.max_age_seconds:
            return "stale"
        return self.quality

    def as_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "source": self.source,
            "quality": self.effective_quality(now),
            "available": self.available,
            "observed_at": _iso(self.observed_at),
            "max_age_seconds": self.max_age_seconds,
            "optional": self.optional,
            "using_cache": self.using_cache,
            "reason": self.reason or None,
        }


@dataclass(frozen=True, slots=True)
class WakePlanningInputs:
    """Already-selected L1 inputs for one local shadow computation."""

    now: datetime
    day_state: str | None
    rules: tuple[WakeRule, ...]
    calendar_decisions: Mapping[date, WakeCalendarDecision] = field(default_factory=dict)
    holidays: Mapping[date, WakeHoliday] = field(default_factory=dict)
    wake_window_minutes: int | None = DEFAULT_WAKE_WINDOW_MINUTES
    routine_duration_minutes: int = DEFAULT_ROUTINE_DURATION_MINUTES
    floor_time: time | None = DEFAULT_WAKE_FLOOR
    calendar_conflict_behavior: str = CONFLICT_WARN_ONLY
    holiday_behavior: str = HOLIDAY_SKIP
    horizon_days: int = DEFAULT_HORIZON_DAYS
    calendar_date: date | None = None
    source_status: tuple[WakeInputStatus, ...] = ()
    minimum_sleep_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class WakeDayDecision:
    """Decision for one local calendar date before next-wake projection."""

    day: date
    state: str
    wake_time: time | None
    decided_by: str
    reason: str
    holiday_name: str | None = None
    vacation: bool = False
    skip_active: bool = False
    next_wake: datetime | None = None
    wake_window_start: datetime | None = None
    wake_window_end: datetime | None = None
    matched_rule_id: str | None = None
    calendar_conflict: bool = False
    calendar_conflict_time: time | None = None
    calendar_suggested_wake_time: time | None = None
    floor_time: time | None = DEFAULT_WAKE_FLOOR
    floor_applied: bool = False
    automatic_day_profile: str | None = None


@dataclass(frozen=True, slots=True)
class WakePlan:
    """Core-State shadow output, safe to expose as read-only attributes."""

    calculated_at: datetime
    calendar_date: date
    day_state: str | None
    state: str
    wake_time: time | None
    next_wake: datetime | None
    wake_needed: bool | None
    wake_window_start: datetime | None
    wake_window_end: datetime | None
    wake_window_minutes: int | None
    minimum_sleep_minutes: int | None
    minimum_sleep_status: str
    decided_by: str
    reason: str
    holiday_active: bool
    holiday_name: str | None
    vacation: bool
    automatic_day_profile: str | None
    matched_rule_id: str | None
    calendar_conflict: bool
    calendar_conflict_time: time | None
    calendar_suggested_wake_time: time | None
    floor_time: time | None
    floor_applied: bool
    source_status: str
    source_quality: str
    source_basis: Mapping[str, Any]
    input_status: tuple[WakeInputStatus, ...]

    @property
    def core_available(self) -> bool:
        return self.source_status not in {"unavailable", "invalid"}

    def safe_decision(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "wake_time": _time_text(self.wake_time),
            "next_wake": _iso(self.next_wake),
            "wake_needed": self.wake_needed,
            "holiday_active": self.holiday_active,
            "automatic_day_profile": self.automatic_day_profile,
            "matched_rule_id": self.matched_rule_id,
            "floor_applied": self.floor_applied,
        }

    def as_attributes(
        self,
        comparison: ShadowComparison | None = None,
    ) -> dict[str, Any]:
        """Render the bounded owner-local diagnostic contract."""

        comparison = comparison or ShadowComparison(
            status=COMPARISON_LEGACY_UNAVAILABLE,
            reason="legacy_reference_not_provided",
            old_decision={},
            core_decision=self.safe_decision(),
        )
        return {
            "shadow_version": SHADOW_VERSION,
            "source": "internal:core_state.wake_planning_shadow",
            "calculated_at": _iso(self.calculated_at),
            "calendar_date": self.calendar_date.isoformat(),
            "day_state": self.day_state,
            "state": self.state,
            "wake_time": _time_text(self.wake_time),
            "next_wake": _iso(self.next_wake),
            "wake_needed": self.wake_needed,
            "wake_window_minutes": self.wake_window_minutes,
            "wake_window_start": _iso(self.wake_window_start),
            "wake_window_end": _iso(self.wake_window_end),
            "minimum_sleep_minutes": self.minimum_sleep_minutes,
            "minimum_sleep_status": self.minimum_sleep_status,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "automatic_day_profile": self.automatic_day_profile,
            "holiday_active": self.holiday_active,
            "holiday_name": self.holiday_name,
            "vacation": self.vacation,
            "matched_rule_id": self.matched_rule_id,
            "calendar_conflict": self.calendar_conflict,
            "calendar_conflict_time": _time_text(self.calendar_conflict_time),
            "calendar_suggested_wake_time": _time_text(self.calendar_suggested_wake_time),
            "floor_time": _time_text(self.floor_time),
            "floor_applied": self.floor_applied,
            "source_status": self.source_status,
            "source_quality": self.source_quality,
            "source_basis": dict(self.source_basis),
            "inputs": {item.name: item.as_dict(self.calculated_at) for item in self.input_status},
            "comparison_status": comparison.status,
            "comparison_reason": comparison.reason,
            "old_decision": dict(comparison.old_decision),
            "core_decision": dict(comparison.core_decision),
            "field_diff": dict(comparison.field_diffs),
        }


@dataclass(frozen=True, slots=True)
class LegacyReference:
    """Safe subset of the old output used for a comparison only."""

    state: str | None = None
    wake_time: time | None = None
    next_wake: datetime | None = None
    wake_needed: bool | None = None
    holiday_active: bool | None = None
    source: str = "legacy:ha_wake_planner"
    quality: str = "fresh"
    available: bool = False
    manual_control_active: bool = False

    def safe_decision(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "wake_time": _time_text(self.wake_time),
            "next_wake": _iso(self.next_wake),
            "wake_needed": self.wake_needed,
            "holiday_active": self.holiday_active,
        }


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    status: str
    reason: str
    old_decision: Mapping[str, Any]
    core_decision: Mapping[str, Any]
    field_diffs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "old_decision": dict(self.old_decision),
            "core_decision": dict(self.core_decision),
            "field_diff": dict(self.field_diffs),
        }


def plan_wake(inputs: WakePlanningInputs) -> WakePlan:
    """Compute one deterministic Core-State shadow result."""

    now = inputs.now
    calendar_date = inputs.calendar_date or (now.date() if isinstance(now, datetime) else date.min)
    fatal = _fatal_input_status(inputs, now, calendar_date)
    source_status, source_quality = _aggregate_input_status(inputs, now)

    if fatal is not None:
        return _unavailable_plan(
            inputs,
            calendar_date,
            source_status=fatal[0],
            source_quality=fatal[1],
            reason=fatal[2],
        )

    current = _decide_for_date(inputs, calendar_date)
    next_wake = _find_next_wake(inputs, now, calendar_date)
    wake_needed = _wake_needed(current, now, inputs.wake_window_minutes)

    # A missing window is a diagnostic degradation for the Boolean only.  The
    # plan itself remains useful and independently comparable.
    if inputs.wake_window_minutes is None:
        source_status = "degraded" if source_status == "available" else source_status
        source_quality = "missing_wake_window"
    elif source_status == "available" and any(
        item.effective_quality(now) in {"missing", "unavailable", "invalid"}
        for item in inputs.source_status
        if item.optional
    ):
        source_status = "degraded"

    holiday = _holiday_for_day(inputs, calendar_date)
    return WakePlan(
        calculated_at=now,
        calendar_date=calendar_date,
        day_state=inputs.day_state,
        state=current.state,
        wake_time=current.wake_time,
        next_wake=next_wake,
        wake_needed=wake_needed,
        wake_window_start=current.wake_window_start,
        wake_window_end=current.wake_window_end,
        wake_window_minutes=inputs.wake_window_minutes,
        minimum_sleep_minutes=inputs.minimum_sleep_minutes,
        minimum_sleep_status=(
            "configured" if inputs.minimum_sleep_minutes is not None else "missing"
        ),
        decided_by=current.decided_by,
        reason=current.reason,
        holiday_active=bool(holiday.is_holiday),
        holiday_name=current.holiday_name,
        vacation=holiday.is_vacation,
        automatic_day_profile=current.automatic_day_profile,
        matched_rule_id=current.matched_rule_id,
        calendar_conflict=current.calendar_conflict,
        calendar_conflict_time=current.calendar_conflict_time,
        calendar_suggested_wake_time=current.calendar_suggested_wake_time,
        floor_time=inputs.floor_time,
        floor_applied=current.floor_applied,
        source_status=source_status,
        source_quality=source_quality,
        source_basis=_source_basis(inputs, calendar_date),
        input_status=inputs.source_status,
    )


def compare_shadow(plan: WakePlan, legacy: LegacyReference | None) -> ShadowComparison:
    """Compare only safe decision fields and explain every non-match."""

    core = plan.safe_decision()
    if not plan.core_available:
        return ShadowComparison(
            status=COMPARISON_CORE_UNAVAILABLE,
            reason=f"core_state_{plan.source_status}",
            old_decision=legacy.safe_decision() if legacy else {},
            core_decision=core,
        )
    if legacy is None or not legacy.available:
        return ShadowComparison(
            status=COMPARISON_LEGACY_UNAVAILABLE,
            reason="legacy_reference_unavailable",
            old_decision=legacy.safe_decision() if legacy else {},
            core_decision=core,
        )
    if legacy.manual_control_active:
        return ShadowComparison(
            status=COMPARISON_NOT_DECIDABLE,
            reason="legacy_manual_override_is_not_in_issue_26_scope",
            old_decision=legacy.safe_decision(),
            core_decision=core,
        )

    old = legacy.safe_decision()
    fields = ("state", "wake_time", "next_wake", "wake_needed", "holiday_active")
    diffs: dict[str, dict[str, Any]] = {}
    comparable = 0
    for field_name in fields:
        old_value = old.get(field_name)
        core_value = core.get(field_name)
        if old_value is None:
            continue
        comparable += 1
        if not _same_value(old_value, core_value, field_name):
            diffs[field_name] = {"legacy": old_value, "core_state": core_value}

    if not comparable:
        return ShadowComparison(
            status=COMPARISON_LEGACY_UNAVAILABLE,
            reason="legacy_has_no_comparable_decision_fields",
            old_decision=old,
            core_decision=core,
        )
    if diffs:
        return ShadowComparison(
            status=COMPARISON_DIFFERENT,
            reason="field_level_decision_difference",
            old_decision=old,
            core_decision=core,
            field_diffs=diffs,
        )
    reason = "same_decision"
    if plan.source_status == "stale":
        reason = "same_decision_with_stale_core_input"
    elif plan.source_status == "degraded":
        reason = "same_decision_with_degraded_core_input"
    return ShadowComparison(
        status=COMPARISON_SAME,
        reason=reason,
        old_decision=old,
        core_decision=core,
    )


def legacy_reference_from_values(
    *,
    state: Any = None,
    wake_time: Any = None,
    next_wake: Any = None,
    wake_needed: bool | None = None,
    holiday_active: bool | None = None,
    now: datetime,
    source: str = "legacy:ha_wake_planner",
    quality: str = "fresh",
    manual_control_active: bool = False,
) -> LegacyReference:
    """Build a comparison reference without retaining legacy free text."""

    parsed_state = str(state) if state in WAKE_STATES else None
    parsed_next = parse_datetime(next_wake, now)
    parsed_wake = parse_time(wake_time)
    available = any(
        value is not None
        for value in (parsed_state, parsed_wake, parsed_next, wake_needed, holiday_active)
    )
    return LegacyReference(
        state=parsed_state,
        wake_time=parsed_wake,
        next_wake=parsed_next,
        wake_needed=wake_needed,
        holiday_active=holiday_active,
        source=source,
        quality=quality,
        available=available,
        manual_control_active=manual_control_active or parsed_state == WAKE_STATE_OVERRIDDEN,
    )


def parse_datetime(value: Any, now: datetime) -> datetime | None:
    """Parse a legacy timestamp in local context, never using UTC as a date."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=now.tzinfo)
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed_time = parse_time(raw)
    if parsed_time is not None and len(raw) <= 8:
        return datetime.combine(now.date(), parsed_time, tzinfo=now.tzinfo)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now.tzinfo)


def parse_manual_holiday_dates(
    configured_dates: Any,
    start: date,
    end: date,
) -> dict[date, WakeHoliday]:
    """Parse the old manual holiday/vacation interval syntax safely."""

    if not configured_dates:
        return {}
    values = (
        re.split(r"[,;\n]+", configured_dates)
        if isinstance(configured_dates, str)
        else configured_dates
    )
    if not isinstance(values, Iterable):
        return {}
    out: dict[date, WakeHoliday] = {}
    for raw_item in values:
        item = str(raw_item or "").strip()
        if not item:
            continue
        match = re.fullmatch(r"(.+?)\s*(?:\.\.|/|to|bis)\s*(.+)", item, re.I)
        if match:
            _add_manual_range(out, match.group(1), match.group(2), start, end)
        else:
            _add_manual_range(out, item, item, start, end)
    return out


def calendar_decisions_from_events(
    events: Sequence[Mapping[str, Any]] | None,
    *,
    skip_titles: Sequence[str] = DEFAULT_CALENDAR_SKIP_TITLES,
    wake_pattern: str = DEFAULT_CALENDAR_WAKE_PATTERN,
) -> dict[date, WakeCalendarDecision]:
    """Parse HA-calendar-shaped event dictionaries without retaining titles."""

    if not events:
        return {}
    try:
        pattern = re.compile(wake_pattern or DEFAULT_CALENDAR_WAKE_PATTERN, re.I)
    except re.error:
        pattern = re.compile(DEFAULT_CALENDAR_WAKE_PATTERN, re.I)
    skip_set = {str(value).strip().lower() for value in skip_titles if str(value).strip()}
    direct: dict[date, WakeCalendarDecision] = {}
    early: dict[date, WakeCalendarDecision] = {}
    for event in events:
        day = _event_date(event)
        if day is None:
            continue
        summary = str(event.get("summary") or event.get("title") or "")
        normalized = summary.strip().lower()
        all_day = bool(event.get("all_day")) or _raw_is_date(event)
        if all_day and normalized in skip_set:
            direct[day] = WakeCalendarDecision(skip=True, source="calendar")
            continue
        match = pattern.search(summary)
        if match:
            value = match.groupdict().get("time") if match.groupdict() else match.group(1)
            wake = parse_time(value)
            if wake is not None:
                direct[day] = WakeCalendarDecision(wake_time=wake, source="calendar")
                continue
        event_time = _event_time(event)
        if event_time is not None:
            candidate = WakeCalendarDecision(
                early_event_time=event_time,
                source="calendar",
            )
            previous = early.get(day)
            if previous is None or event_time < (previous.early_event_time or time.max):
                early[day] = candidate
    for day, decision in early.items():
        direct.setdefault(day, decision)
    return direct


def _decide_for_date(inputs: WakePlanningInputs, day: date) -> WakeDayDecision:
    holiday = _holiday_for_day(inputs, day)
    holiday_name = holiday.safe_name()
    profile = "weekend" if day.weekday() >= 5 or holiday.is_holiday else "weekday"
    calendar = inputs.calendar_decisions.get(day)

    if calendar and calendar.skip:
        return WakeDayDecision(
            day=day,
            state=WAKE_STATE_SKIPPED,
            wake_time=None,
            decided_by="calendar",
            reason="calendar_skip_marker",
            holiday_name=holiday_name,
            vacation=holiday.is_vacation,
            automatic_day_profile=profile,
        )
    if calendar and calendar.wake_time:
        return _build_decision(
            inputs,
            day,
            calendar.wake_time,
            WAKE_STATE_SCHEDULED,
            "calendar",
            "calendar_wake_marker",
            holiday=holiday,
            automatic_day_profile=profile,
        )

    matched = _match_rule(inputs.rules, day, holiday.is_holiday)
    if matched is None:
        if holiday.is_holiday and inputs.holiday_behavior == HOLIDAY_WEEKEND_PROFILE:
            saturday = _first_saturday_rule(inputs.rules)
            if saturday and saturday.wake_time:
                return _build_decision(
                    inputs,
                    day,
                    saturday.wake_time,
                    WAKE_STATE_SCHEDULED,
                    "holiday_fallback",
                    "holiday_to_weekend_profile",
                    holiday=holiday,
                    matched_rule_id=saturday.id,
                    automatic_day_profile=profile,
                )
        return WakeDayDecision(
            day=day,
            state=WAKE_STATE_HOLIDAY if holiday.is_holiday else WAKE_STATE_INACTIVE,
            wake_time=None,
            decided_by="holiday" if holiday.is_holiday else "no_rule",
            reason="holiday_without_wake_rule" if holiday.is_holiday else "no_matching_rule",
            holiday_name=holiday_name,
            vacation=holiday.is_vacation,
            automatic_day_profile=profile,
        )

    if matched.action == RULE_ACTION_SKIP:
        return WakeDayDecision(
            day=day,
            state=WAKE_STATE_SKIPPED,
            wake_time=None,
            decided_by=f"rule:{matched.id}",
            reason="rule_skip",
            holiday_name=holiday_name,
            vacation=holiday.is_vacation,
            matched_rule_id=matched.id,
            automatic_day_profile=profile,
        )

    decision = _build_decision(
        inputs,
        day,
        matched.wake_time,
        WAKE_STATE_SCHEDULED,
        f"rule:{matched.id}",
        "rule_wake",
        holiday=holiday,
        matched_rule_id=matched.id,
        automatic_day_profile=profile,
    )
    return _apply_calendar_conflict(inputs, decision, calendar)


def _build_decision(
    inputs: WakePlanningInputs,
    day: date,
    requested_time: time | None,
    state: str,
    decided_by: str,
    reason: str,
    *,
    holiday: WakeHoliday,
    matched_rule_id: str | None = None,
    automatic_day_profile: str | None = None,
) -> WakeDayDecision:
    if requested_time is None:
        return WakeDayDecision(
            day=day,
            state=WAKE_STATE_INACTIVE,
            wake_time=None,
            decided_by="invalid_rule",
            reason="wake_time_missing",
            holiday_name=holiday.safe_name(),
            vacation=holiday.is_vacation,
            matched_rule_id=matched_rule_id,
            automatic_day_profile=automatic_day_profile,
        )
    final_time = requested_time.replace(second=0, microsecond=0, tzinfo=None)
    floor_applied = False
    if inputs.floor_time is not None and final_time < inputs.floor_time:
        final_time = inputs.floor_time
        floor_applied = True
        reason = f"{reason};absolute_floor_applied"
    wake_dt = datetime.combine(day, final_time, tzinfo=inputs.now.tzinfo)
    window_start: datetime | None = None
    window_end: datetime | None = None
    if inputs.wake_window_minutes is not None and inputs.wake_window_minutes >= 0:
        window = timedelta(minutes=inputs.wake_window_minutes)
        window_start = wake_dt - window
        window_end = wake_dt + window
    return WakeDayDecision(
        day=day,
        state=state,
        wake_time=final_time,
        decided_by=decided_by,
        reason=reason,
        holiday_name=holiday.safe_name(),
        vacation=holiday.is_vacation,
        next_wake=wake_dt,
        wake_window_start=window_start,
        wake_window_end=window_end,
        matched_rule_id=matched_rule_id,
        floor_time=inputs.floor_time,
        floor_applied=floor_applied,
        automatic_day_profile=automatic_day_profile,
    )


def _apply_calendar_conflict(
    inputs: WakePlanningInputs,
    decision: WakeDayDecision,
    calendar: WakeCalendarDecision | None,
) -> WakeDayDecision:
    if (
        calendar is None
        or calendar.early_event_time is None
        or decision.wake_time is None
        or inputs.calendar_conflict_behavior == CONFLICT_IGNORE
    ):
        return decision
    event_dt = datetime.combine(
        decision.day,
        calendar.early_event_time,
        tzinfo=inputs.now.tzinfo,
    )
    suggested_dt = event_dt - timedelta(minutes=max(inputs.routine_duration_minutes, 0))
    wake_dt = datetime.combine(
        decision.day,
        decision.wake_time,
        tzinfo=inputs.now.tzinfo,
    )
    if suggested_dt >= wake_dt:
        return decision
    suggested_time = suggested_dt.time().replace(second=0, microsecond=0, tzinfo=None)
    if inputs.calendar_conflict_behavior == CONFLICT_WAKE_EARLIER:
        adjusted = _build_decision(
            inputs,
            decision.day,
            suggested_time,
            WAKE_STATE_SCHEDULED,
            "calendar_conflict",
            "calendar_conflict_wake_earlier",
            holiday=_holiday_for_day(inputs, decision.day),
            matched_rule_id=decision.matched_rule_id,
            automatic_day_profile=decision.automatic_day_profile,
        )
        return replace(
            adjusted,
            calendar_conflict=True,
            calendar_conflict_time=calendar.early_event_time,
            calendar_suggested_wake_time=suggested_time,
        )
    if inputs.calendar_conflict_behavior == CONFLICT_WARN_ONLY:
        return replace(
            decision,
            reason="calendar_conflict_warn_only",
            calendar_conflict=True,
            calendar_conflict_time=calendar.early_event_time,
            calendar_suggested_wake_time=suggested_time,
        )
    return decision


def _match_rule(rules: Sequence[WakeRule], day: date, is_holiday: bool) -> WakeRule | None:
    for rule in sorted(rules, key=lambda item: (item.priority, item.name, item.id)):
        if not rule.enabled:
            continue
        if rule.action == RULE_ACTION_WAKE and rule.wake_time is None:
            continue
        if _rule_matches(rule, day, is_holiday):
            return rule
    return None


def _rule_matches(rule: WakeRule, day: date, is_holiday: bool) -> bool:
    if rule.on_holiday is not None and bool(is_holiday) != rule.on_holiday:
        return False
    if rule.weekdays is not None and day.weekday() not in rule.weekdays:
        return False
    if rule.date_from is not None and day < rule.date_from:
        return False
    if rule.date_to is not None and day > rule.date_to:
        return False
    if rule.specific_dates is not None and day not in rule.specific_dates:
        return False
    if rule.week_interval is not None and rule.week_anchor is not None and rule.week_interval > 0:
        day_week_start = day - timedelta(days=day.weekday())
        anchor_week_start = rule.week_anchor - timedelta(days=rule.week_anchor.weekday())
        weeks_since = (day_week_start - anchor_week_start).days // 7
        if weeks_since < 0 or weeks_since % rule.week_interval != 0:
            return False
    if (
        rule.cycle_anchor is not None
        and rule.cycle_length is not None
        and rule.cycle_slot_start is not None
        and rule.cycle_slot_length is not None
        and rule.cycle_length > 0
    ):
        offset = (day - rule.cycle_anchor).days % rule.cycle_length
        if (
            offset < rule.cycle_slot_start
            or offset >= rule.cycle_slot_start + rule.cycle_slot_length
        ):
            return False
    return True


def _first_saturday_rule(rules: Sequence[WakeRule]) -> WakeRule | None:
    for rule in sorted(rules, key=lambda item: (item.priority, item.name, item.id)):
        if (
            rule.enabled
            and rule.action == RULE_ACTION_WAKE
            and rule.wake_time
            and rule.weekdays
            and 5 in rule.weekdays
        ):
            return rule
    return None


def _holiday_for_day(inputs: WakePlanningInputs, day: date) -> WakeHoliday:
    if day.weekday() >= 5:
        # Saturday is explicitly weekend; the old source also labels weekend
        # dates as holiday/day-off for the holiday_active projection.
        existing = inputs.holidays.get(day)
        return WakeHoliday(
            is_holiday=True,
            name="Weekend",
            is_vacation=bool(existing and existing.is_vacation),
            source="calendar_weekend",
            quality="fresh",
        )
    return inputs.holidays.get(day, WakeHoliday(is_holiday=False))


def _find_next_wake(
    inputs: WakePlanningInputs,
    now: datetime,
    current_day: date,
) -> datetime | None:
    horizon = max(0, int(inputs.horizon_days))
    for offset in range(horizon + 1):
        candidate_day = current_day + timedelta(days=offset)
        decision = _decide_for_date(inputs, candidate_day)
        if decision.state != WAKE_STATE_SCHEDULED or decision.wake_time is None:
            continue
        candidate = datetime.combine(candidate_day, decision.wake_time, tzinfo=now.tzinfo)
        if candidate >= now:
            return candidate
    return None


def _wake_needed(
    decision: WakeDayDecision,
    now: datetime,
    window_minutes: int | None,
) -> bool | None:
    if window_minutes is None:
        return None
    if decision.state != WAKE_STATE_SCHEDULED:
        return False
    if decision.wake_window_start is None or decision.wake_window_end is None:
        return False
    return decision.wake_window_start <= now <= decision.wake_window_end


def _source_basis(inputs: WakePlanningInputs, calendar_date: date) -> dict[str, Any]:
    by_name = {item.name: item for item in inputs.source_status}
    return {
        "local_time": "home_assistant:local_civil_time",
        "calendar_date": calendar_date.isoformat(),
        "day_state": "internal:logic.compute_day_state",
        "rules": by_name["rules"].source if "rules" in by_name else "internal:default_rules",
        "holiday": by_name["holiday"].source if "holiday" in by_name else "not_configured",
        "calendar": by_name["calendar"].source if "calendar" in by_name else "not_configured",
        "floor": "internal:absolute_local_floor",
        "raw_data": "none; L1 orchestration inputs only",
    }


def _fatal_input_status(
    inputs: WakePlanningInputs,
    now: datetime,
    calendar_date: date,
) -> tuple[str, str, str] | None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return ("invalid", "invalid", "local_time_must_be_timezone_aware")
    if inputs.calendar_date is not None and inputs.calendar_date != now.date():
        return ("invalid", "invalid", "local_calendar_date_mismatch")
    if inputs.floor_time is None:
        return ("invalid", "invalid", "wake_floor_missing_or_invalid")
    if not inputs.rules:
        return ("unavailable", "missing", "wake_rules_unavailable")
    return None


def _aggregate_input_status(
    inputs: WakePlanningInputs,
    now: datetime,
) -> tuple[str, str]:
    effective_by_name = {item.name: item.effective_quality(now) for item in inputs.source_status}
    rules_quality = effective_by_name.get("rules")
    if rules_quality == "invalid":
        return "invalid", "invalid_rules"
    if rules_quality in {"missing", "unavailable"}:
        return "unavailable", "missing_rules"
    effective = list(effective_by_name.values())
    if any(value == "invalid" for value in effective):
        return "invalid", "invalid_input"
    if any(value == "stale" for value in effective):
        return "stale", "stale_input"
    if any(value in {"missing", "unavailable"} for value in effective):
        return "degraded", "missing_input"
    return "available", "fresh"


def _unavailable_plan(
    inputs: WakePlanningInputs,
    calendar_date: date,
    *,
    source_status: str,
    source_quality: str,
    reason: str,
) -> WakePlan:
    now = inputs.now
    return WakePlan(
        calculated_at=now,
        calendar_date=calendar_date,
        day_state=inputs.day_state,
        state=WAKE_STATE_INACTIVE,
        wake_time=None,
        next_wake=None,
        wake_needed=None,
        wake_window_start=None,
        wake_window_end=None,
        wake_window_minutes=inputs.wake_window_minutes,
        minimum_sleep_minutes=inputs.minimum_sleep_minutes,
        minimum_sleep_status=(
            "configured" if inputs.minimum_sleep_minutes is not None else "missing"
        ),
        decided_by="core_state_unavailable",
        reason=reason,
        holiday_active=False,
        holiday_name=None,
        vacation=False,
        automatic_day_profile=None,
        matched_rule_id=None,
        calendar_conflict=False,
        calendar_conflict_time=None,
        calendar_suggested_wake_time=None,
        floor_time=inputs.floor_time,
        floor_applied=False,
        source_status=source_status,
        source_quality=source_quality,
        source_basis=_source_basis(inputs, calendar_date),
        input_status=inputs.source_status,
    )


def parse_manual_holiday_date(value: str, year: int) -> date | None:
    """Parse one old yearly or full-date token."""

    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.fullmatch(r"\d{8}", value):
            return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
        if re.fullmatch(r"\d{2}-\d{2}", value):
            return date.fromisoformat(f"{year}-{value}")
        if re.fullmatch(r"\d{4}", value):
            return date.fromisoformat(f"{year}-{value[:2]}-{value[2:]}")
    except ValueError:
        return None
    return None


def _add_manual_range(
    target: dict[date, WakeHoliday],
    raw_start: str,
    raw_end: str,
    map_start: date,
    map_end: date,
) -> None:
    for year in range(map_start.year, map_end.year + 1):
        start = parse_manual_holiday_date(raw_start, year)
        end = parse_manual_holiday_date(raw_end, year)
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        current = max(start, map_start)
        last = min(end, map_end)
        while current <= last:
            target[current] = WakeHoliday(
                is_holiday=True,
                name="Manual holiday",
                is_vacation=True,
                source="manual_holiday_dates",
                quality="fresh",
            )
            current += timedelta(days=1)
        if len(raw_start.strip()) >= 8 and len(raw_end.strip()) >= 8:
            return


def _event_date(event: Mapping[str, Any]) -> date | None:
    raw = event.get("start") or event.get("start_time") or event.get("date")
    if isinstance(raw, Mapping):
        raw = raw.get("dateTime") or raw.get("date")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return parse_date(raw[:10])
    return None


def _event_time(event: Mapping[str, Any]) -> time | None:
    raw = event.get("start") or event.get("start_time")
    if isinstance(raw, Mapping):
        raw = raw.get("dateTime")
    if isinstance(raw, datetime):
        return raw.timetz().replace(tzinfo=None, second=0, microsecond=0)
    if not isinstance(raw, str) or len(raw) <= 10:
        return None
    try:
        return (
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
            .time()
            .replace(tzinfo=None, second=0, microsecond=0)
        )
    except ValueError:
        return None


def _raw_is_date(event: Mapping[str, Any]) -> bool:
    raw = event.get("start") or event.get("start_time") or event.get("date")
    if isinstance(raw, Mapping):
        return bool(raw.get("date") and not raw.get("dateTime"))
    return (
        isinstance(raw, str)
        and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw[:10]))
        and len(raw) <= 10
    )


def _same_value(left: Any, right: Any, field_name: str) -> bool:
    if field_name == "next_wake":
        left_dt = (
            parse_datetime(left, datetime.min.replace(tzinfo=UTC))
            if isinstance(left, str)
            else left
        )
        right_dt = (
            parse_datetime(right, datetime.min.replace(tzinfo=UTC))
            if isinstance(right, str)
            else right
        )
        if isinstance(left_dt, datetime) and isinstance(right_dt, datetime):
            return left_dt.astimezone(UTC) == right_dt.astimezone(UTC)
    return bool(left == right)


def _age_seconds(now: datetime, observed_at: datetime) -> float:
    if now.tzinfo is None:
        if observed_at.tzinfo is not None:
            observed_at = observed_at.replace(tzinfo=None)
    elif observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - observed_at).total_seconds())


def _time_text(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
