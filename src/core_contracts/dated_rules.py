"""Date projections and configurable daily intervals; no domain time constants."""

from datetime import UTC, date, datetime, time, timedelta
from math import ceil

from . import declarative_models as dm
from .calendar_rules import annual_curve
from .clock import berlin_utc, berlin_zone
from .quality import FieldValue, ReasonCode, derive, unknown
from .schema import FieldSchema


def dated_rule(
    rule: dm.CalendarAtRule | dm.DailyWindowRule,
    args: list[FieldValue],
    now: datetime,
    due: list[str],
) -> FieldValue:
    if not args[0].usable(now):
        return args[0]
    try:
        text = args[0].value
        day = (
            date.fromisoformat(text)
            if len(text) == 10
            else datetime.fromisoformat(text).astimezone(berlin_zone()).date()
        )
        midnight = berlin_utc(datetime.combine(day + timedelta(days=1), time()))
        if midnight > now:
            due.append(midnight.isoformat())
        if isinstance(rule, dm.CalendarAtRule):
            day += timedelta(days=rule.offset_days)
            value = (
                day.isoformat()
                if rule.field == "date"
                else day.isoweekday()
                if (rule.field == "weekday")
                else berlin_utc(
                    datetime.combine(day + timedelta(days=int(rule.field == "end")), time())
                ).isoformat()
            )
            return derive(value, args, now)

        def boundary(on: date, phase: str) -> datetime:
            points = rule.phase_curves[phase]
            spec = dm.AnnualCurveRule(
                op="annual_curve",
                at=dm.LiteralRule(
                    op="literal",
                    schema=FieldSchema(type="text", required=True, physical_state=False),
                    value=on.isoformat(),
                ),
                points=points,
            )
            result = annual_curve(
                spec, [FieldValue(status="valid", quality="healthy", value=on.isoformat())], now
            )
            if not result.usable(now) or not 0 <= result.value < 1440:
                raise ValueError("daily_boundary_invalid")
            return berlin_utc(datetime.combine(on, time()) + timedelta(minutes=ceil(result.value)))

        end_today = boundary(day, rule.end_phase)
        morning = day if now < end_today else day + timedelta(days=1)
        workday = args[2] if morning == day else args[1]
        if not workday.usable(now):
            if [arg.value for arg in args[3:6]] != [arg.value for arg in args[6:9]]:
                return derive(None, [workday], now)
            selected = args[3:6]
        else:
            selected = args[3:6] if workday.value is True else args[6:9]
        if any(not item.usable(now) for item in selected):
            return derive(None, selected, now)
        kind, phase, clock = (item.value for item in selected)
        end = boundary(morning, rule.end_phase)

        def begin(on: date) -> datetime:
            if kind == "phase":
                return boundary(on, phase)
            if kind != "time" or not isinstance(clock, str):
                raise ValueError("daily_window_mode")
            parsed = time.fromisoformat(clock)
            if len(clock) != 5 or parsed.tzinfo is not None:
                raise ValueError("daily_window_time")
            return berlin_utc(datetime.combine(on, parsed))

        start = begin(morning)
        if start == end:
            raise ValueError("daily_window_empty")
        if start > end:
            start = begin(morning - timedelta(days=1))
        for stamp in (start, end):
            if stamp > now:
                due.append(stamp.isoformat())
        decisive = [args[0], *selected, *([workday] if workday.usable(now) else [])]
        return derive(start <= now.astimezone(UTC) < end, decisive, now)
    except ValueError, TypeError, KeyError, OverflowError:
        return unknown(ReasonCode.INVALID_VALUE, rule.op, now)
