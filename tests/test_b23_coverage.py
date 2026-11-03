from datetime import UTC, datetime, timedelta

import pytest

from core_contracts.calendar_coverage import add_months, status

from .conftest import activate
from .test_b22_p8 import holiday_config, regular_coverage, sweep


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
    assert "Lücke vom" in checked["message"] and "mehr als 4 Monate" in checked["message"]
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
