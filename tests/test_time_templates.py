"""Production engines against a compact reference extracted from pinned legacy code."""

import csv
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core_contracts.calendar_inputs import CalendarInput, project
from core_contracts.clock import FakeClock
from core_contracts.declarative import Evaluation, compile_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.persistence import State
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.registry import Contract

from .test_calendar_rules import NOW, calendar, event

ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = {
    d["type_id"]: DeclarativeDefinition.model_validate(d)
    for d in json.loads((ROOT / "examples/time-templates-v1.json").read_text(encoding="utf-8"))
}
PHASES = list(DEFINITIONS["day.phase"].rules)
SEASONS = ["spring", "summer", "autumn", "winter"]
# Deliberately synthetic thresholds, independent of installation configuration.
PARAMETERS = {
    f"{season}_{key}": 10 + i * 20 + offset
    for i, season in enumerate(SEASONS)
    for key, offset in [("dark", 0), ("bright", 10)]
}


class Harness:
    def __init__(self, name, parameters=None):
        self.definition = DEFINITIONS[name]
        self.kind = compile_definition(self.definition)
        self.clock, self.node, self.latches = FakeClock(NOW), {}, {}
        self.contract = Contract(
            contract_id="test.time",
            type_id=name,
            type_version=1,
            enabled=True,
            inputs=[],
            parameters=parameters or {},
        )
        self.state = State(active_revision=1, epoch_id="synthetic")

    def evaluate(self, **values):
        inputs = {
            key: raw
            if isinstance(raw, FieldValue)
            else unknown(ReasonCode.INPUT_STALE, key, self.clock.now_utc())
            if raw is None
            else FieldValue(
                status="valid", quality="healthy", value=raw, evidence=("synthetic:" + key,)
            )
            for key, raw in values.items()
        }
        fields, self.node, _ = self.kind.evaluate(
            self.clock,
            self.latches,
            self.contract,
            inputs,
            self.node,
            self.state,
            "input",
            "valid",
            None,
        )
        return fields["value"]


def test_all_seven_declarative_templates_compile_without_new_python_types():
    assert len(DEFINITIONS) == 7
    for name in DEFINITIONS:
        Harness(name)


def test_1096_days_all_nine_boundaries_match_actual_pinned_legacy_first_full_minute():
    model = DEFINITIONS["day.phase"]
    with (ROOT / "tests/fixtures/day-phase-legacy-2026-2028.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1096
    h = Harness("day.phase")
    for row in rows:
        evaluation = Evaluation(
            model, {"date": FieldValue(status="valid", quality="healthy", value=row["date"])}, NOW
        )
        for phase in PHASES:
            result = evaluation.evaluate(model.rules[phase])
            assert result.status == "valid"
            assert math.ceil(result.value) == int(row[phase]), (row["date"], phase, result.value)
            minute = int(row[phase])
            i = PHASES.index(phase)
            if minute:
                assert h.evaluate(date=row["date"], minute=minute - 1).value == PHASES[i - 1]
            assert h.evaluate(date=row["date"], minute=minute).value == phase


@pytest.mark.parametrize(
    "day",
    [
        "2026-01-01",
        "2026-03-21",
        "2026-03-29",
        "2026-06-21",
        "2026-09-23",
        "2026-10-25",
        "2026-12-21",
        "2026-12-31",
        "2028-01-15",
        "2028-02-09",
        "2028-02-29",
    ],
)
def test_phase_at_minute_before_and_first_full_minute_and_midnight(day):
    with (ROOT / "tests/fixtures/day-phase-legacy-2026-2028.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        row = next(row for row in csv.DictReader(stream) if row["date"] == day)
    h = Harness("day.phase")
    for i, phase in enumerate(PHASES):
        minute = int(row[phase])
        if minute:
            assert h.evaluate(date=day, minute=minute - 1).value == PHASES[i - 1]
        assert h.evaluate(date=day, minute=minute).value == phase
    assert h.evaluate(date=day, minute=1439).value == "late_evening"
    assert h.evaluate(date=day, minute=0).value == (
        "early_night" if int(row["early_night"]) == 0 else "late_evening"
    )
    assert h.evaluate(date=None, minute=0).status == "unknown"


def test_autumn_repeated_hour_evaluates_current_wall_minute_without_replay():
    h = Harness("day.phase")
    for raw in ["2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"]:
        now = datetime.fromisoformat(raw)
        minute, _ = project(CalendarInput(projection="minute_of_day"), now)
        assert minute == 150
        assert h.evaluate(date="2026-10-25", minute=minute).value == "early_night"


@pytest.mark.parametrize(
    "weekday,holiday,events,expected",
    [
        (1, False, [], "werktag"),
        (6, False, [], "wochenende"),
        (7, False, [], "wochenende"),
        (1, True, [], "frei"),
        (1, False, [dict(summary="Urlaub", start="2026-10-24", end="2026-10-27")], "frei"),
        (1, False, [event()], "werktag"),
        (1, False, [event(title="SYNTHETIC_WORK")], "werktag"),
    ],
)
def test_context_holiday_vacation_weekend_and_weekday(weekday, holiday, events, expected):
    h = Harness("day.context", {"vacation_pattern": "*Urlaub*"})
    result = h.evaluate(weekday=weekday, holiday=holiday, calendar=calendar(events))
    assert result.value == expected and result.quality == "healthy"
    assert "SYNTHETIC_PRIVATE" not in result.model_dump_json() + json.dumps(h.node)


def test_context_fallback_retains_quality_in_the_explicit_not_free_branch():
    h = Harness("day.context", {"vacation_pattern": "*Urlaub*"})
    result = h.evaluate(weekday=1, holiday=False, calendar=None)
    assert result.value == "werktag" and result.quality == "degraded"
    assert {ReasonCode.PARTIAL_EVIDENCE, ReasonCode.INPUT_STALE} <= {r.code for r in result.reasons}
    assert h.evaluate(weekday=None, holiday=False, calendar=None).status == "unknown"
    assert h.evaluate(weekday=None, holiday=True, calendar=None).value == "frei"


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-02-28", "winter"),
        ("2028-02-29", "winter"),
        ("2026-03-01", "spring"),
        ("2026-06-01", "summer"),
        ("2026-09-01", "autumn"),
        ("2026-12-01", "winter"),
    ],
)
def test_meteorological_season_at_local_midnight(day, expected):
    now = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Berlin")).astimezone(UTC)
    projection = CalendarInput(
        projection="season",
        seasons=[
            dict(name=season, start=start)
            for season, start in zip(SEASONS, ["03-01", "06-01", "09-01", "12-01"], strict=True)
        ],
    )
    raw, due = project(projection, now)
    assert due > now
    assert Harness("season.meteorological").evaluate(season=raw).value == expected


@pytest.mark.parametrize("season", SEASONS)
def test_seasonal_brightness_exact_schmitt_edges_unknown_restart_and_parameter_order(season):
    h = Harness("outdoor.brightness", PARAMETERS)
    dark, bright = PARAMETERS[season + "_dark"], PARAMETERS[season + "_bright"]
    assert h.evaluate(lux=dark, season=season).status == "unknown"
    assert h.evaluate(lux=bright, season=season).value == "bright"
    assert h.evaluate(lux=dark, season=season).value == "bright"
    assert h.evaluate(lux=dark - 0.01, season=season).value == "dark"
    assert h.evaluate(lux=bright - 0.01, season=season).value == "dark"
    assert h.evaluate(lux=None, season=season).status == "unknown"
    assert h.evaluate(lux=dark + 1, season=season).status == "unknown"
    h.node, h.latches = {}, {}
    assert h.evaluate(lux=dark + 1, season=season).status == "unknown"
    invalid = {**PARAMETERS, season + "_dark": bright}
    with pytest.raises(ValueError):
        h.kind.parameters.model_validate(invalid)


def test_season_change_rechecks_same_lux_without_renewing_its_measurement():
    h = Harness("outdoor.brightness", PARAMETERS)
    measured = FieldValue(
        status="valid",
        quality="healthy",
        value=25,
        measured_at=NOW,
        valid_until=NOW + timedelta(seconds=8400),
    )
    assert h.evaluate(lux=measured, season="spring").value == "bright"
    h.clock.advance(60)
    result = h.evaluate(lux=measured, season="summer")
    assert (
        result.value == "dark"
        and result.measured_at == NOW
        and result.valid_until == measured.valid_until
    )


@pytest.mark.parametrize("name", ["sun.elevation", "sun.azimuth", "outdoor.illuminance"])
def test_numeric_sources_preserve_quality_measurement_and_unknown(name):
    h = Harness(name)
    raw = FieldValue(status="valid", quality="healthy", value=12.5, measured_at=NOW)
    assert h.evaluate(measurement=raw) == raw
    assert h.evaluate(measurement=None).status == "unknown"
