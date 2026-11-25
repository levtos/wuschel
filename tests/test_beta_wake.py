"""Issue-61 additions around the unchanged original planner."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from core_contracts.calendar_coverage import add_months
from core_contracts.extensions.wake.operator import build_plan
from core_contracts.extensions.wake.settings import WakeException, WakeSettings
from core_contracts.freshness_templates import upgrade_document
from core_contracts.quality import CalendarCoverage, FieldValue

from .conftest import activate
from .test_b22_p8 import holiday_config, regular_coverage, sweep
from .test_beta_sleep import configuration, fields, observe, settings


def valid(value):
    return FieldValue(status="valid", quality="healthy", value=value)


def plan(now, *, capture=None, exception=None, events=(), holidays=(), coverage=None, **changes):
    config = settings()
    for profile in config["profiles"].values():
        profile.update(wake_time="08:00", minimum_sleep_minutes=480, wake_window_minutes=15)
    config.update(changes)
    cache = dict(
        window_start=(now - timedelta(days=1)).isoformat(),
        window_end=(now + timedelta(days=32)).isoformat(),
        fetched_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    holiday_cache = valid({**cache, "events": list(holidays)})
    if coverage is not None:
        holiday_cache = holiday_cache.with_calendar_coverage(coverage)
    return build_plan(
        WakeSettings.model_validate(config),
        now,
        valid({**cache, "events": list(events)}),
        holiday_cache,
        valid(now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()),
        valid(capture.isoformat())
        if capture
        else FieldValue(status="not_applicable", quality="healthy", positively_not_applicable=True),
        valid(exception) if exception else None,
    )


def test_thursday_evening_plan_is_unknown_if_holiday_gap_starts_at_friday_midnight():
    now = datetime(2026, 1, 1, 19, tzinfo=UTC)
    friday_midnight = datetime(2026, 1, 1, 23, tzinfo=UTC)
    coverage = CalendarCoverage("source.holidays", friday_midnight, now + timedelta(days=1))
    result = plan(now, coverage=coverage)["all"]
    assert result.status == "unknown" and result.value is None
    assert result.reasons[0].code == "calendar_coverage_missing"
    assert result.reasons[0].input == "source.holidays"
    assert result.reasons[0].detail


def test_gap_in_six_months_preserves_the_plan_and_caps_its_validity():
    now = datetime(2026, 1, 1, 19, tzinfo=UTC)
    boundary = add_months(now, 6)
    coverage = CalendarCoverage("source.holidays", boundary, add_months(now, 9))
    original = plan(now)
    result = plan(now, coverage=coverage)
    assert result["plan_date"].value == "2026-01-02"
    assert result == {
        name: field.model_copy(update={"valid_until": boundary}) for name, field in original.items()
    }


async def test_coverage_expiry_reevaluates_wake_plan_and_survives_restore(runtime, config):
    document = configuration(config)
    holidays = upgrade_document(holiday_config(config))
    document["dependencies"].extend(holidays["dependencies"])
    for table in ("sources", "bindings"):
        document[table] = [
            row for row in document[table] if row["source_id"] != "source.holiday_days"
        ]
    document["sources"].append({**holidays["sources"][0], "source_id": "source.holiday_days"})
    binding = {**holidays["bindings"][0], "source_id": "source.holiday_days"}
    binding["adapter"]["window"].update(past_days=0, future_days=31)
    document["bindings"].append(binding)
    await activate(runtime, document)
    now = runtime.clock.now_utc()
    await observe(runtime, "local_date", "2026-01-01", kind="snapshot")
    await observe(
        runtime,
        "appointments",
        dict(
            window_start=(now - timedelta(days=1)).isoformat(),
            window_end=(now + timedelta(days=32)).isoformat(),
            fetched_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
            events=[],
        ),
        kind="snapshot",
    )
    await sweep(runtime, regular_coverage(runtime, now + timedelta(days=1)))
    assert fields(runtime)["wake_next_wake_start"]["status"] == "valid"

    # Shorten only the persisted proof while keeping the fact cache fresh.
    expiry = now + timedelta(seconds=1)
    state = runtime.state.clone()
    rows = state.tables["service_state"]
    coverage = rows["calendar_coverage:binding.a"]
    coverage["expires_at"] = expiry.isoformat()
    boundary = min(coverage["evidence_end"], coverage["horizon"])
    assert datetime.fromisoformat(rows["calendar:binding.a"]["expires_at"]) > expiry
    await runtime.store.commit(state)
    runtime.state = await runtime.store.load()
    await runtime._restore("process_restart")
    assert fields(runtime)["wake_next_wake_start"]["status"] == "valid"
    assert datetime.fromisoformat(fields(runtime)["wake_next_wake_start"]["valid_until"]) == expiry
    assert runtime.next_evaluation == expiry
    deadlines = runtime.state.tables["node_state"]["fixture.sleep"]["clock_due"]
    assert {expiry.isoformat(), boundary}.issubset(deadlines)

    runtime.clock.advance(1)
    await runtime.submit("tick")
    for restore in (False, True):
        if restore:
            await runtime._restore("process_restart")
        result = fields(runtime)["wake_next_wake_start"]
        assert result["status"] == "unknown" and result["value"] is None
        assert result["reasons"][0]["code"] == "calendar_coverage_missing"
        assert result["reasons"][0]["input"] == "source.holiday_days"


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2032, 3, 28, 6, tzinfo=UTC), datetime(2032, 3, 28, 11, tzinfo=UTC)),
        (datetime(2032, 10, 31, 6, tzinfo=UTC), datetime(2032, 10, 31, 11, tzinfo=UTC)),
    ],
)
def test_minimum_sleep_uses_real_elapsed_time_across_dst_and_shifts_window(now, expected):
    capture = now.replace(hour=3)
    result = plan(now, capture=capture)
    assert result["next_wake_start"].value == expected.isoformat()
    assert datetime.fromisoformat(result["window_start"].value) == expected - timedelta(minutes=15)
    assert datetime.fromisoformat(result["window_end"].value) == expected + timedelta(minutes=15)
    assert result["minimum_sleep_applied"].value is True


def test_minimum_sleep_does_not_apply_to_workday_or_pre_sleep():
    now = datetime(2032, 4, 5, 5, tzinfo=UTC)  # Monday
    assert plan(now, capture=now)["next_wake_start"].value == "2032-04-05T06:00:00+00:00"
    weekend = datetime(2032, 4, 4, 5, tzinfo=UTC)
    assert plan(weekend)["next_wake_start"].value == "2032-04-04T06:00:00+00:00"


def test_manual_exception_is_bound_to_its_date_and_skip_advances_horizon():
    now = datetime(2032, 4, 5, 5, tzinfo=UTC)
    result = plan(now, exception={"date": "2032-04-05", "wake_time": "09:00"})
    assert result["next_wake_start"].value == "2032-04-05T07:00:00+00:00"
    result = plan(now, exception={"date": "2032-04-05", "skip": True})
    assert result["status"].value == "skipped"
    assert result["plan_date"].value == "2032-04-06"


@pytest.mark.parametrize(
    "value",
    [
        {"date": "2032-02-30", "wake_time": "09:00"},
        {"date": "2032-04-05"},
        {"date": "2032-04-05", "wake_time": "09:00", "skip": True},
        {"date": "2032-04-05", "wake_time": "25:00"},
    ],
)
def test_exception_rejects_ambiguous_or_invalid_values(value):
    with pytest.raises(ValidationError):
        WakeException.model_validate(value)


def test_calendar_bounds_and_regex_are_enforced_inline():
    now = datetime(2032, 4, 5, 5, tzinfo=UTC)
    event = dict(
        summary="x" * 257,
        start=now.isoformat(),
        end=(now + timedelta(hours=1)).isoformat(),
        all_day=False,
        key="synthetic",
    )
    assert plan(now, events=[event])["all"].status == "unknown"
    assert plan(now, events=[{**event, "summary": "synthetic"}] * 1001)["all"].status == "unknown"
    with pytest.raises(ValidationError):
        WakeSettings.model_validate({**settings(), "calendar_wake_pattern": "x" * 257})
    with pytest.raises(ValidationError):
        WakeSettings.model_validate({**settings(), "calendar_wake_pattern": "("})


def test_already_passed_base_time_is_shifted_before_filtering_candidates():
    now = datetime(2032, 4, 4, 9, tzinfo=UTC)
    assert (
        plan(now, capture=now.replace(hour=3))["next_wake_start"].value
        == "2032-04-04T11:00:00+00:00"
    )


def test_day_thirty_is_included_and_thirty_one_skipped_days_leave_no_candidate():
    now = datetime(2032, 1, 1, 1, tzinfo=UTC)
    title = settings()["calendar_skip_titles"][0]
    midnight = now.astimezone(ZoneInfo("Europe/Berlin")).replace(hour=0)
    events = [
        dict(
            summary=title,
            start=(midnight + timedelta(days=i)).isoformat(),
            end=(midnight + timedelta(days=i + 1)).isoformat(),
            all_day=True,
            key="synthetic",
        )
        for i in range(31)
    ]
    assert plan(now, events=events[:30])["plan_date"].value == "2032-01-31"
    assert plan(now, events=events)["next_wake_start"].status == "not_applicable"
