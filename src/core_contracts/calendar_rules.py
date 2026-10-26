"""Bounded calendar operators; dates use days, event intervals use UTC instants."""

import re
from datetime import date, datetime, time, timedelta
from fnmatch import fnmatchcase
from fractions import Fraction
from typing import Any

from . import declarative_models as dm
from .clock import berlin_utc
from .model import canonical, utc
from .quality import FieldValue, ReasonCode, derive, unknown


def annual_curve(rule: dm.AnnualCurveRule, args: list[FieldValue], now: datetime) -> FieldValue:
    if not args[0].usable(now) and args[0].value is None:
        return args[0]
    if any(not arg.usable(now) for arg in args):
        return derive(None, args, now)
    try:
        raw = args[0].value
        if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ValueError
        today = date.fromisoformat(raw)
        # Gregorian dates repeat after 400 years; keep both neighbour years representable.
        today = today.replace(year=2000 + today.year % 400)
        parameters = iter(args[1:])
        values = [
            Fraction(
                str(next(parameters).value if isinstance(p.value, dm.ParameterRule) else p.value)
            )
            for p in rule.points
        ]
        anchors = []
        # Neighbour years provide both sides of the circular year boundary.
        for year in range(today.year - 1, today.year + 2):
            for point, value in zip(rule.points, values, strict=True):
                day = point.day
                try:
                    stamp = date.fromisoformat(f"{year:04d}-{day}")
                except ValueError:
                    if day != "02-29" or rule.leap_day == "skip":
                        continue
                    stamp = date(year, 2, 28) if rule.leap_day == "feb28" else date(year, 3, 1)
                anchors.append((stamp, value))
        anchors.sort()
        left = max((p for p in anchors if p[0] <= today), default=None)
        right = min((p for p in anchors if p[0] > today), default=None)
        if left is None or right is None:
            raise ValueError
        result = left[1] + (right[1] - left[1]) * Fraction(
            (today - left[0]).days, (right[0] - left[0]).days
        )
        return derive(float(result), args, now)
    except ValueError, TypeError, OverflowError:
        return unknown(ReasonCode.INVALID_VALUE, "annual_curve", now)


def calendar_active(
    rule: dm.CalendarActiveRule,
    args: list[FieldValue],
    now: datetime,
    due: list[str],
) -> FieldValue:
    for arg in args:
        if arg.valid_from and arg.valid_from > now:
            due.append(arg.valid_from.isoformat())
    if not args[0].usable(now) and args[0].value is None:
        return args[0]
    if any(not arg.usable(now) for arg in args):
        return derive(None, args, now)
    try:
        cache, pattern = args[0].value, args[1].value
        if (
            not isinstance(cache, dict)
            or not isinstance(pattern, str)
            or not pattern.strip()
            or len(pattern) > 256
        ):
            raise ValueError
        if len(canonical(cache).encode()) > 1048576:
            raise ValueError

        def stamp(value: Any) -> datetime:
            if not isinstance(value, str):
                raise ValueError
            return utc(datetime.fromisoformat(value))

        start, end = stamp(cache["window_start"]), stamp(cache["window_end"])
        fetched, expires = stamp(cache["fetched_at"]), stamp(cache["expires_at"])
        events = cache["events"]
        if start >= end or fetched > now or fetched >= expires:
            raise ValueError
        if not isinstance(events, list) or len(events) > 1000:
            raise ValueError
        day_start = day_end = None
        if rule.on_date:
            day = date.fromisoformat(args[2].value)
            day_start = berlin_utc(datetime.combine(day, time()))
            day_end = berlin_utc(datetime.combine(day + timedelta(days=1), time()))
        intervals = []
        evidence = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError
            a, b = stamp(event["start"]), stamp(event["end"])
            title, key, all_day = event["summary"], event["key"], event["all_day"]
            location = event.get("location", "")
            if (
                a >= b
                or not isinstance(title, str)
                or not isinstance(location, str)
                or max(len(title), len(location)) > 4096
                or type(all_day) is not bool
                or not isinstance(key, str)
                or not re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]*", key)
            ):
                raise ValueError
            if (not rule.all_day_only or all_day) and fnmatchcase(
                title.strip().lower(), pattern.strip().lower()
            ):
                intervals.append((a, b))
                if (a < day_end and b > day_start) if day_start and day_end else a <= now < b:
                    evidence.append(key)
        for deadline in (
            end,
            expires,
            *(a.valid_until for a in args),
            *(a.held_until for a in args),
        ):
            if deadline and deadline > now:
                due.append(deadline.isoformat())
        if now < start:
            due.append(start.isoformat())
            return unknown(ReasonCode.INPUT_NOT_YET_VALID, "calendar_active", now)
        if now >= min(end, expires):
            return unknown(ReasonCode.INPUT_STALE, "calendar_active", now)
        if day_start and day_end:
            if start > day_start or end < day_end:
                return unknown(ReasonCode.INPUT_NOT_YET_VALID, "calendar_active", now)
            result = derive(bool(evidence), args, now)
            return result.model_copy(
                update={
                    "evidence": tuple(dict.fromkeys((*result.evidence, *evidence))),
                    "valid_until": min(end, expires, result.valid_until or expires),
                }
            )
        union: list[tuple[datetime, datetime]] = []
        for a, b in sorted(intervals):
            if union and a <= union[-1][1]:
                union[-1] = (union[-1][0], max(b, union[-1][1]))
            else:
                union.append((a, b))
        active = any(a <= now < b for a, b in union)
        change = next((b if a <= now else a for a, b in union if b > now), None)
        if change:
            due.append(change.isoformat())
        result = derive(active, args, now)
        return result.model_copy(
            update={
                "evidence": tuple(dict.fromkeys((*result.evidence, *evidence))),
                "valid_until": min(end, expires, result.valid_until or expires),
            }
        )
    except ValueError, TypeError, KeyError, OverflowError:
        # Never put source content (titles, locations, patterns) in errors or traces.
        return unknown(ReasonCode.INVALID_VALUE, "calendar_active", now)
