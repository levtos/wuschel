"""Calendar-cache boundary and explicit #61 additions over the unchanged pure port."""

from datetime import date, datetime, time, timedelta
from fnmatch import fnmatchcase
from typing import Any
from zoneinfo import ZoneInfo

from ..model import checksum, utc
from ..quality import FieldValue, ReasonCode, derive, unknown
from . import wake_config as config
from . import wake_planning as planning
from .settings import WakeException, WakeSettings

ZONE = ZoneInfo("Europe/Berlin")


def local_start(day: date) -> datetime:
    return datetime.combine(day, time(), ZONE)


def calendar_events(value: Any, now: datetime, start: date, end: date) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("calendar_invalid")
    a, b, fetched, expires = (
        utc(datetime.fromisoformat(value[name]))
        for name in ("window_start", "window_end", "fetched_at", "expires_at")
    )
    if a > local_start(start) or b < local_start(end) or not fetched <= now < expires:
        raise ValueError("calendar_not_covered")
    events = value["events"]
    if not isinstance(events, list) or len(events) > 1000:
        raise ValueError("calendar_event_limit")
    result = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("summary"), str):
            raise ValueError("calendar_event_invalid")
        if len(event["summary"]) > 256 or type(event.get("all_day")) is not bool:
            raise ValueError("calendar_event_invalid")
        begin, finish = (
            utc(datetime.fromisoformat(event[key])).astimezone(ZONE) for key in ("start", "end")
        )
        if begin >= finish:
            raise ValueError("calendar_event_invalid")
        result.append(
            {
                "summary": event["summary"],
                "start": begin,
                "end": finish,
                "all_day": event["all_day"],
                "key": event["key"],
            }
        )
    return result


def holiday_days(
    events: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    vacation: bool = False,
    pattern: str | None = None,
) -> dict[date, planning.WakeHoliday]:
    result = {}
    for event in events:
        if (
            not event["all_day"]
            or pattern is not None
            and not fnmatchcase(event["summary"].strip().lower(), pattern.strip().lower())
        ):
            continue
        day = max(start, event["start"].date())
        while day < end and local_start(day) < event["end"]:
            result[day] = planning.WakeHoliday(True, is_vacation=vacation, source="calendar")
            day += timedelta(days=1)
    return result


def build_plan(
    settings: WakeSettings,
    now: datetime,
    calendar: FieldValue,
    holidays: FieldValue,
    local_date: FieldValue,
    sleep_since: FieldValue,
    exception: FieldValue | None,
    consumed: tuple[str, ...] = (),
) -> dict[str, FieldValue]:
    inputs = [calendar, holidays, local_date]
    if sleep_since.status != "not_applicable" and not sleep_since.usable(now):
        return {"all": derive(None, [*inputs, sleep_since], now)}
    if any(not value.usable(now) for value in inputs):
        return {"all": derive(None, inputs, now)}
    try:
        today = date.fromisoformat(local_date.value)
        if today != now.astimezone(ZONE).date():
            raise ValueError("local_calendar_date_mismatch")
        end = today + timedelta(days=31)
        appointments = calendar_events(calendar.value, now, today, end)
        holiday_events = calendar_events(holidays.value, now, today, end)
    except ValueError, TypeError, KeyError, OverflowError:
        return {"all": unknown(ReasonCode.INVALID_VALUE, "wake_calendar", now)}
    days = holiday_days(holiday_events, today, end)
    days.update(
        holiday_days(appointments, today, end, vacation=True, pattern=settings.vacation_pattern)
    )
    days.update(
        planning.parse_manual_holiday_dates(
            settings.manual_holiday_intervals, today, end - timedelta(days=1)
        )
    )
    # Expand all-day markers over their local dates; timed appointments retain original order.
    expanded = []
    for event in appointments:
        if not event["all_day"]:
            expanded.append(event)
            continue
        day = max(today, event["start"].date())
        while day < end and local_start(day) < event["end"]:
            expanded.append(
                {**event, "start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat()}
            )
            day += timedelta(days=1)
    decisions = planning.calendar_decisions_from_events(
        expanded,
        skip_titles=settings.calendar_skip_titles,
        wake_pattern=settings.calendar_wake_pattern,
    )
    original = settings.original()
    rules = config.planning_rules(original)
    override = (
        WakeException.model_validate(exception.value)
        if exception and exception.usable(now)
        else None
    )
    candidates: list[tuple[datetime, datetime, datetime, str, str, str, bool]] = []
    current = None
    for offset in range(31):
        day = today + timedelta(days=offset)
        profile_id = config.profile_id_for_date(day, days)
        profile = original.profile_for(profile_id)
        params = planning.WakePlanningInputs(
            now=now.astimezone(ZONE),
            day_state=None,
            rules=rules,
            holidays=days,
            calendar_decisions=decisions,
            floor_time=planning.parse_time(settings.wake_floor),
            wake_window_minutes=profile.wake_window_minutes,
            minimum_sleep_minutes=profile.minimum_sleep_minutes,
            routine_duration_minutes=settings.routine_duration_minutes,
            calendar_conflict_behavior=settings.calendar_conflict_behavior,
            holiday_behavior=settings.holiday_behavior,
        )
        decision = planning._decide_for_date(params, day)
        if override and override.date == day.isoformat():
            if override.skip:
                decision = planning.WakeDayDecision(day, "skipped", None, "manual", "manual_skip")
            else:
                decision = planning._build_decision(
                    params,
                    day,
                    planning.parse_time(override.wake_time),
                    "scheduled",
                    "manual",
                    "manual_time",
                    holiday=planning._holiday_for_day(params, day),
                )
        if offset == 0:
            current = decision
        if decision.next_wake is None:
            continue
        base = utc(decision.next_wake)
        key = checksum(
            {
                "date": day.isoformat(),
                "base": base.isoformat(),
                "settings": settings.model_dump(mode="json"),
                "reason": decision.reason,
            }
        )
        effective = base
        if profile_id == "weekend" and profile.minimum_sleep_minutes and sleep_since.usable(now):
            effective = max(
                base,
                utc(datetime.fromisoformat(sleep_since.value))
                + timedelta(minutes=profile.minimum_sleep_minutes),
            )
        if effective < now or key in consumed:
            continue
        window = timedelta(minutes=profile.wake_window_minutes)
        candidates.append(
            (
                effective,
                effective - window,
                effective + window,
                day.isoformat(),
                key,
                decision.reason,
                effective != base,
            )
        )
    assert current is not None
    selected = min(candidates, key=lambda row: row[0]) if candidates else None
    if exception and exception.usable(now):
        inputs.append(exception)
    if sleep_since.usable(now) and selected and selected[6]:
        inputs.append(sleep_since)
    values: dict[str, Any] = {
        "status": current.state,
        "next_wake_start": selected[0].isoformat() if selected else None,
        "window_start": selected[1].isoformat() if selected else None,
        "window_end": selected[2].isoformat() if selected else None,
        "plan_date": selected[3] if selected else None,
        "candidate_key": selected[4] if selected else None,
        "decision": selected[5] if selected else current.reason,
        "minimum_sleep_applied": bool(selected and selected[6]),
    }
    result = {}
    for name, value in values.items():
        if value is None:
            result[name] = FieldValue(
                status="not_applicable",
                quality="healthy",
                positively_not_applicable=True,
                evidence=tuple(e for item in inputs for e in item.evidence),
            )
        else:
            result[name] = derive(value, inputs, now)
    return result
