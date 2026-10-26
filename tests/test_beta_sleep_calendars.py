"""Claude's #61 calendar decision: shared bounded caches preserve today's meaning."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core_contracts.calendar_cache import CalendarWindow, normalize_events
from core_contracts.declarative import Evaluation, compile_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.quality import FieldValue

ROOT = Path(__file__).parents[1]
TIME = json.loads((ROOT / "examples/time-templates-v1.json").read_text(encoding="utf-8"))
SLEEP = json.loads((ROOT / "examples/sleep-templates-v1.json").read_text(encoding="utf-8"))
CONTEXT = DeclarativeDefinition.model_validate(
    next(d for d in TIME if d["type_id"] == "day.context")
)


def valid(value):
    return FieldValue(status="valid", quality="healthy", value=value)


def cache(now, events, *, past, future):
    start, end = CalendarWindow(past_days=past, future_days=future).bounds(now)
    return valid(
        dict(
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            fetched_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
            events=normalize_events(
                {"calendar.synthetic": {"events": events}},
                "calendar.synthetic",
                start,
                end,
                "Europe/Berlin",
            ),
        )
    )


def current(now, events, holiday, past, future):
    return Evaluation(
        CONTEXT,
        {
            "weekday": valid(now.isoweekday()),
            "holiday": valid(holiday),
            "calendar": cache(now, events, past=past, future=future),
        },
        now,
        parameters={"vacation_pattern": "*Urlaub*"},
    ).evaluate(CONTEXT.outputs["value"])


@pytest.mark.parametrize("offset", range(7))
def test_week_of_unchanged_day_context_before_and_after_window_change(offset):
    now = datetime(2032, 4, 5, 10, tzinfo=UTC) + timedelta(days=offset)
    events = [
        dict(summary="Synthetischer Urlaub", start="2032-04-04", end="2032-04-08"),
        dict(
            summary="Synthetischer Termin",
            start="2032-04-09T12:00:00+02:00",
            end="2032-04-09T13:00:00+02:00",
        ),
        dict(
            summary="Synthetischer Urlaub",
            start="2032-04-10T12:00:00+02:00",
            end="2032-04-10T13:00:00+02:00",
        ),
    ]
    for holiday in (False, True):
        before = current(now, events, holiday, 1, 14)
        after = current(now, events, holiday, 0, 31)
        assert before.usable(now) and after.usable(now)
        assert before.value == after.value
        assert after.value == (
            "frei" if holiday or offset < 3 else "werktag" if offset < 5 else "wochenende"
        )


def test_vacation_started_yesterday_is_still_recognized_with_zero_past_days():
    now = datetime(2032, 4, 5, 10, tzinfo=UTC)
    events = [dict(summary="Synthetischer Urlaub", start="2032-04-04", end="2032-04-07")]
    snapshot = cache(now, events, past=0, future=31)
    assert len(snapshot.value["events"]) == 1
    assert datetime.fromisoformat(snapshot.value["events"][0]["start"]) < datetime.fromisoformat(
        snapshot.value["window_start"]
    )
    assert current(now, events, False, 0, 31).value == "frei"


def test_today_and_tomorrow_use_the_same_holiday_and_appointment_cache():
    now = datetime(2032, 4, 5, 10, tzinfo=UTC)
    holidays = cache(
        now,
        [dict(summary="Synthetischer Feiertag", start="2032-04-06", end="2032-04-07")],
        past=0,
        future=31,
    )
    appointments = cache(now, [], past=0, future=31)
    definition = DeclarativeDefinition.model_validate(
        next(d for d in SLEEP if d["type_id"] == "day.context_at")
    )
    for day, expected in (("2032-04-05", "werktag"), ("2032-04-06", "frei")):
        result = Evaluation(
            definition,
            dict(date=valid(day), holidays=holidays, appointments=appointments),
            now,
            parameters={"vacation_pattern": "*Urlaub*"},
        ).evaluate(definition.outputs["value"])
        assert result.value == expected


def test_every_public_template_compiles_including_enum_instance_parameters():
    for template in SLEEP:
        compile_definition(DeclarativeDefinition.model_validate(template))
