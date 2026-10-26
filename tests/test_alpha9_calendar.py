from datetime import UTC, datetime, timedelta

import pytest

from core_contracts.calendar_cache import CalendarWindow, normalize_events
from core_contracts.persistence import PersistenceUnavailable
from core_contracts.registry import validate
from core_contracts.service_jobs import calendar_status, plan_calendars

from .conftest import activate


def calendar_config(config):
    config["config_schema_version"] = 2
    config["sources"][0].update(
        source_type="state",
        value_schema={"type": "object", "required": True, "physical_state": False},
        freshness={"mode": "periodic_ttl", "interval_s": 3600, "future_tolerance_s": 0},
    )
    config["bindings"][0]["adapter"] = {
        "kind": "ha_calendar",
        "ha_entity_id": "calendar.example",
        "window": {"timezone": "Europe/Berlin", "past_days": 1, "future_days": 14},
        "refresh_s": 900,
        "max_age_s": 3600,
    }
    config["bindings"][0].pop("transform", None)
    return config


def field(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]


async def fetch(runtime, data=None, outcome="completed"):
    runtime.bridge = "connected"
    runtime.bridge_protocol_selected = 2
    job = await runtime.submit("service_claim")
    assert job
    await runtime.submit(
        "service_complete",
        {
            "job_id": job["job_id"],
            "response": {
                "outcome": outcome,
                "data": data if data is not None else {"calendar.example": {"events": []}},
            },
        },
    )
    return job


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_local_all_day_half_open_dst_and_duplicate_multiplicity(day, hours):
    today = datetime.fromisoformat(day).replace(tzinfo=UTC)
    window = CalendarWindow(past_days=0, future_days=1)
    start, end = window.bounds(today)
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == hours * 3600
    event = {
        "summary": "fixture",
        "start": day,
        "end": (today + timedelta(days=1)).date().isoformat(),
    }
    events = normalize_events(
        {"calendar.example": {"events": [event, event]}},
        "calendar.example",
        start,
        end,
        "Europe/Berlin",
    )
    assert len(events) == 2 and events[0]["key"] != events[1]["key"]
    assert (
        datetime.fromisoformat(events[0]["end"]) - datetime.fromisoformat(events[0]["start"])
    ).total_seconds() == hours * 3600
    assert (
        normalize_events(
            {
                "calendar.example": {
                    "events": [
                        {
                            **event,
                            "start": end.isoformat(),
                            "end": (end + timedelta(hours=1)).isoformat(),
                        }
                    ]
                }
            },
            "calendar.example",
            start,
            end,
            "Europe/Berlin",
        )
        == []
    )


async def test_cache_survives_disconnect_until_original_expiry_and_restore(runtime, config):
    await activate(runtime, calendar_config(config))
    assert field(runtime)["status"] == "unknown"
    await fetch(runtime)
    assert field(runtime)["value"]["events"] == []
    assert field(runtime)["status"] == "valid"
    fetched = field(runtime)["value"]["fetched_at"]
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    assert field(runtime)["status"] == "valid"
    runtime.clock.advance(901)
    await runtime.submit("tick")
    await fetch(runtime, outcome="indeterminate")
    assert field(runtime)["value"]["fetched_at"] == fetched
    assert field(runtime)["status"] == "valid"
    await runtime._restore("process_restart")
    assert field(runtime)["status"] == "valid"
    assert field(runtime)["value"]["fetched_at"] == fetched
    runtime.clock.advance(2700)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "unknown"
    assert field(runtime)["reasons"][0]["code"] == "input_stale"


async def test_missing_calendar_is_not_valid_empty_and_backoff_is_bounded(runtime, config):
    await activate(runtime, calendar_config(config))
    for delay in (5, 30, 120, 900):
        await fetch(runtime, {"another.calendar": {"events": []}})
        assert field(runtime)["status"] == "unknown"
        row = runtime.state.tables["service_state"]["calendar:binding.a"]
        assert datetime.fromisoformat(row["next_at"]) == runtime.clock.now_utc() + timedelta(
            seconds=delay
        )
        runtime.clock.advance(delay)
        await runtime.submit("tick")


async def test_failure_series_logs_once_resets_on_success_and_exposes_metadata_only(
    runtime, config, caplog
):
    await activate(runtime, calendar_config(config))
    caplog.set_level("WARNING", logger="core_contracts.runtime")

    async def fail(code):
        runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
        job = await runtime.submit("service_claim")
        assert job
        await runtime.submit(
            "service_complete",
            {
                "job_id": job["job_id"],
                "response": {
                    "outcome": "failed",
                    "error_code": code,
                    "data": {"summary": "PRIVATE PROVIDER TEXT"},
                },
            },
        )

    await fail("PRIVATE PROVIDER TEXT")
    runtime.clock.advance(5)
    await runtime.submit("tick")
    await fail("ha_unavailable")
    warnings = [row for row in caplog.records if row.message == "calendar_fetch_failed"]
    assert len(warnings) == 1
    assert warnings[0].binding_id == "binding.a"
    assert warnings[0].error_code == "calendar_fetch_failed"
    assert "PRIVATE PROVIDER TEXT" not in str([vars(row) for row in caplog.records])
    from core_contracts.app import JsonFormatter, TextFormatter

    for formatter in (JsonFormatter(), TextFormatter()):
        rendered = formatter.format(warnings[0])
        assert "binding.a" in rendered and "calendar_fetch_failed" in rendered
        assert "PRIVATE PROVIDER TEXT" not in rendered
    status = calendar_status(runtime.state, runtime.config)
    assert status == [
        {
            "binding_id": "binding.a",
            "source_id": "source.a",
            "fetched_at": None,
            "expires_at": None,
            "error_code": "ha_unavailable",
        }
    ]
    runtime.clock.advance(30)
    await runtime.submit("tick")
    await fetch(runtime)
    healthy = calendar_status(runtime.state, runtime.config)[0]
    assert healthy["error_code"] is None
    assert datetime.fromisoformat(healthy["expires_at"]) > runtime.clock.now_utc()
    runtime.clock.advance(900)
    await runtime.submit("tick")
    await fail("timeout")
    assert len([row for row in caplog.records if row.message == "calendar_fetch_failed"]) == 2
    assert calendar_status(runtime.state, runtime.config)[0]["expires_at"] == healthy["expires_at"]
    plan_calendars(runtime.state, runtime.config, runtime.clock.now_utc() + timedelta(days=1))
    assert (
        runtime.state.tables["service_state"]["calendar:binding.a"]["failure_warning_emitted"]
        is True
    )


async def test_commit_failure_prevents_dispatch_and_late_result_cannot_replace_new_generation(
    runtime, config
):
    await activate(runtime, calendar_config(config))
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    runtime.store.available = False
    with pytest.raises(PersistenceUnavailable):
        await runtime.submit("service_claim")
    assert next(iter(runtime.state.tables["service_job"].values()))["status"] == "planned"
    runtime.store.available = True
    await runtime.submit("recover")
    runtime.bridge = "connected"
    job = await runtime.submit("service_claim")
    assert job
    changed = calendar_config(config)
    changed["bindings"][0]["adapter"]["ha_entity_id"] = "calendar.new"
    await activate(runtime, changed)
    await runtime.submit(
        "service_complete",
        {
            "job_id": job["job_id"],
            "response": {"outcome": "completed", "data": {"calendar.example": {"events": []}}},
        },
    )
    assert field(runtime)["status"] == "unknown"
    assert not runtime.state.tables["source_observation_current"]


async def test_inflight_restart_is_indeterminate_and_does_not_refresh_cache(runtime, config):
    await activate(runtime, calendar_config(config))
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    job = await runtime.submit("service_claim")
    await runtime._restore("process_restart")
    assert runtime.state.tables["service_job"][job["job_id"]]["status"] == "indeterminate"
    assert field(runtime)["status"] == "unknown"
    assert await runtime.submit("service_claim") is None


def test_calendar_requires_schema_two_and_finite_matching_freshness(runtime, config):
    document = calendar_config(config)
    validate(document, runtime.types)
    document["config_schema_version"] = 1
    with pytest.raises(ValueError):
        validate(document, runtime.types)
    document["config_schema_version"] = 2
    document["sources"][0]["freshness"]["interval_s"] = 9999
    with pytest.raises(ValueError):
        validate(document, runtime.types)
