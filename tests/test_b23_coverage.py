from datetime import UTC, datetime, timedelta

import pytest

from core_contracts.calendar_coverage import add_months, identity, status
from core_contracts.clock import berlin_zone
from core_contracts.registry import RegistryConfig
from core_contracts.service_jobs import calendar_status
from dev.build_media_templates import literal

from .conftest import activate
from .test_alpha9_calendar import fetch
from .test_b22_p8 import holiday_config, regular_coverage, sweep
from .test_beta_media import field


@pytest.mark.parametrize("gap", ["internal", "tail"])
async def test_long_gaps_are_reported_despite_future_events(runtime, config, gap):
    await activate(runtime, holiday_config(config))
    first = runtime.clock.now_utc() + timedelta(days=1)
    events = regular_coverage(runtime, first)
    if gap == "internal":
        del events[1:3]
    else:
        events = events[:1]
    await sweep(runtime, events)
    checked = status(runtime.state, runtime.config.bindings[0], 12, runtime.clock.now_utc())
    assert checked["complete"] and not checked["covered"]
    assert checked["gap"] is not None
    assert (
        "Lücke vom" in checked["message"]
        and "erlaubt sind höchstens 4 Monate" in checked["message"]
    )
    row = runtime.state.tables["service_state"]["calendar_coverage:binding.a"]
    assert "summary" not in str(row)  # Only date evidence survives the sweep.


def test_four_month_boundary_uses_calendar_months_and_leap_days():
    assert add_months(datetime(2026, 10, 31, tzinfo=UTC), 4).date().isoformat() == "2027-02-28"


async def test_healthy_full_horizon_is_scanned_past_first_event(runtime, config):
    await activate(runtime, holiday_config(config))
    await sweep(runtime, regular_coverage(runtime, runtime.clock.now_utc() + timedelta(days=1)))
    checked = status(runtime.state, runtime.config.bindings[0], 12, runtime.clock.now_utc())
    assert checked["covered"] and checked["message"] is None and checked["gap"] is None
    probes = [job for job in runtime.state.tables["service_job"].values() if job.get("coverage")]
    assert len(probes) >= 12


@pytest.mark.parametrize("extra_days,covered", [(0, True), (1, False)])
async def test_exact_four_month_gap_is_allowed_but_one_day_more_is_not(
    runtime, config, extra_days, covered
):
    await activate(runtime, holiday_config(config))
    first = runtime.clock.now_utc() + timedelta(days=1)
    dates = [first]
    for _ in range(2):
        dates.append(add_months(dates[-1] + timedelta(hours=1), 4) + timedelta(days=extra_days))
    events = [
        {"start": day.isoformat(), "end": (day + timedelta(hours=1)).isoformat()} for day in dates
    ]
    await sweep(runtime, events)
    assert (
        status(runtime.state, runtime.config.bindings[0], 12, runtime.clock.now_utc())["covered"]
        is covered
    )


@pytest.mark.parametrize("limit,covered", [(None, False), (4, False), (5, True), (3, False)])
async def test_source_gap_limit_controls_coverage(runtime, config, limit, covered):
    document = holiday_config(config)
    if limit is not None:
        document["sources"][0]["calendar_max_gap_months"] = limit
    await activate(runtime, document)
    now = runtime.clock.now_utc()
    first = now + timedelta(days=1)
    second = add_months(first + timedelta(hours=1), 4) + timedelta(days=6)
    events = [
        {"start": first.isoformat(), "end": (first + timedelta(hours=1)).isoformat()},
        *regular_coverage(runtime, second),
    ]
    await sweep(runtime, events)
    checked = calendar_status(runtime.state, runtime.config, now)[0]["coverage"]

    assert checked["complete"] and checked["covered"] is covered
    assert checked["max_gap_months"] == (limit or 4)
    if covered:
        assert checked["gap"] is None and checked["message"] is None
    else:
        assert checked["gap"] is not None
        assert f"(erlaubt sind höchstens {limit or 4} Monate)." in checked["message"]


def test_gap_limit_changes_coverage_identity(config):
    binding = RegistryConfig.model_validate(holiday_config(config)).bindings[0]
    assert len({identity(binding, 12, limit) for limit in (None, 4, 5)}) == 3


@pytest.mark.parametrize("limit", [0, 13, "5", 5.0])
def test_gap_limit_requires_an_integer_in_range(config, limit):
    document = holiday_config(config)
    document["sources"][0]["calendar_max_gap_months"] = limit
    with pytest.raises(ValueError, match="calendar_max_gap_months"):
        RegistryConfig.model_validate(document)


def test_gap_limit_requires_coverage(config):
    document = holiday_config(config)
    source = document["sources"][0]
    del source["calendar_coverage_months"]
    source["calendar_max_gap_months"] = 5
    with pytest.raises(ValueError, match="calendar_max_gap_requires_coverage"):
        RegistryConfig.model_validate(document)


def dated_holiday_config(config, today):
    document = holiday_config(config)
    contract, template = document["contracts"][0], document["definitions"][0]
    document["contracts"] = []
    document["definitions"] = []
    for offset in (0, 1):
        type_id = f"fixture.holiday_{offset}"
        on_date = {
            "op": "calendar_at",
            "at": literal(today.isoformat()),
            "field": "date",
            "offset_days": offset,
        }
        rule = {**template["outputs"]["value"], "on_date": on_date}
        document["definitions"].append({**template, "type_id": type_id, "outputs": {"value": rule}})
        document["contracts"].append(
            {**contract, "type_id": type_id, "contract_id": f"fixture.day_{offset}"}
        )
    return document


def dated_results(runtime):
    return [field(runtime, contract=f"fixture.day_{offset}") for offset in (0, 1)]


async def test_far_gap_warns_without_rejecting_today_or_tomorrow_after_restore(runtime, config):
    now = runtime.clock.now_utc()
    today = now.astimezone(berlin_zone()).date()
    await activate(runtime, dated_holiday_config(config, today))
    assert all(result["status"] == "unknown" for result in dated_results(runtime))
    events = [
        {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
        for start in (add_months(now, months) for months in (1, 3, 6, 11))
    ]
    await sweep(runtime, events)
    checked = calendar_status(runtime.state, runtime.config, now)[0]["coverage"]
    assert checked["complete"] and not checked["covered"]
    assert datetime.fromisoformat(checked["gap"]["start"]) == add_months(now, 6) + timedelta(
        hours=1
    )
    assert [result["value"] for result in dated_results(runtime)] == [False, False]
    await runtime._restore("process_restart")
    assert [result["status"] for result in dated_results(runtime)] == ["valid", "valid"]
    assert [result["value"] for result in dated_results(runtime)] == [False, False]


@pytest.mark.parametrize("internal_gap", [True, False])
async def test_today_is_valid_when_tomorrow_is_in_gap_or_beyond_proof(
    runtime, config, internal_gap
):
    now = runtime.clock.now_utc()
    today = now.astimezone(berlin_zone()).date()
    tomorrow = today + timedelta(days=1)
    document = dated_holiday_config(config, today)
    if not internal_gap:
        document["sources"][0]["calendar_coverage_months"] = 1
    await activate(runtime, document)
    events = [{"start": today.isoformat(), "end": tomorrow.isoformat()}]
    if internal_gap:
        events.extend(regular_coverage(runtime, add_months(now, 5)))
    await sweep(runtime, events)

    row = calendar_status(runtime.state, runtime.config, now)[0]
    if row["fetched_at"] is None:
        job = await fetch(runtime, {"calendar.example": {"events": events}})
        assert not job.get("coverage")
    checked = row["coverage"]
    assert checked["complete"]
    assert (checked["gap"] is not None) is internal_gap
    current, next_day = dated_results(runtime)
    assert current["status"] == "valid" and current["value"] is True, current
    assert next_day["status"] == "unknown" and next_day["value"] is None
    assert next_day["reasons"][0]["code"] == "calendar_coverage_missing"


async def test_expired_coverage_is_unknown_even_with_fresh_local_events(runtime, config):
    now = runtime.clock.now_utc()
    today = now.astimezone(berlin_zone()).date()
    await activate(runtime, dated_holiday_config(config, today))
    await sweep(runtime, regular_coverage(runtime, now + timedelta(days=1)))
    assert [result["status"] for result in dated_results(runtime)] == ["valid", "valid"]

    runtime.state = runtime.state.clone()
    rows = runtime.state.tables["service_state"]
    expiry = now + timedelta(seconds=1)
    rows["calendar_coverage:binding.a"]["expires_at"] = expiry.isoformat()
    assert datetime.fromisoformat(rows["calendar:binding.a"]["expires_at"]) > expiry
    runtime._plan_evaluation()
    assert runtime.next_evaluation == expiry
    runtime.clock.advance(1)
    await runtime.submit("tick")
    assert runtime.state.tables["service_state"]["calendar_coverage:binding.a"]["complete"]
    for result in dated_results(runtime):
        assert result["status"] == "unknown" and result["value"] is None
        assert result["reasons"][0]["code"] == "calendar_coverage_missing"
