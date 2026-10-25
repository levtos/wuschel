"""Explicit device clocks never silently fall back to HA event timestamps."""

from datetime import timedelta

import pytest

from core_contracts.adapters.ha import BridgeAdapter, device_timestamp, timestamp
from core_contracts.evidence import Freshness, assess

from .conftest import activate


def observation(runtime, raw, *, selected=True, state="on", include=True):
    config = runtime.config
    binding = config.bindings[0].model_copy(
        update={
            "adapter": config.bindings[0].adapter.model_copy(
                update={"ha_time_attribute": "clock" if selected else None}
            )
        }
    )
    stamp = runtime.clock.now_utc().isoformat()
    data = {
        "state": state,
        "attributes": {"clock": raw} if include else {},
        "last_changed": stamp,
        "last_updated": stamp,
        "last_reported": stamp,
    }
    return BridgeAdapter(runtime, None, "ws://unused", "test").observation(
        binding, data, "report", {"time_fired": stamp}, config
    )


@pytest.mark.parametrize("raw", [123, 1700000000, "1700000000", 1700000000.5, "1700000000.5"])
async def test_numeric_device_seconds_are_utc_and_used_by_real_adapter(runtime, config, raw):
    await activate(runtime, config)
    item = observation(runtime, raw)
    assert not item.invalid_timestamp and item.device_time == device_timestamp(raw)
    assert item.device_time.utcoffset() == timedelta(0)
    assert item.measurement.timestamp() == float(raw)
    with pytest.raises(ValueError):
        timestamp(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        False,
        "NaN",
        "Infinity",
        "1e400",
        "broken",
        "2026-01-01T12:00:00",
        1700000000000000000,
    ],
)
async def test_missing_or_invalid_device_clock_is_never_ha_evidence(runtime, config, raw):
    await activate(runtime, config)
    item = observation(runtime, raw)
    assert item.invalid_timestamp and item.device_time is None and item.measurement is None
    assert item.ha_last_reported is not None
    assert assess(item, config_policy(), runtime.clock.now_utc(), "source.a").status == "unknown"
    dependent = observation(runtime, None, selected=False)
    result = assess(dependent, liveness_policy(), runtime.clock.now_utc(), "source.b", item)
    assert result.status == "unknown" and result.reasons[0].code == "dependency_unavailable"


def config_policy():
    return Freshness(mode="report_heartbeat", interval_s=60, future_tolerance_s=0)


def liveness_policy():
    return Freshness(
        mode="liveness_source", interval_s=60, future_tolerance_s=0, liveness_source="source.a"
    )


async def test_absent_attribute_future_clock_and_frozen_device_reports(runtime, config):
    await activate(runtime, config)
    assert observation(runtime, None, include=False).invalid_timestamp
    now = runtime.clock.now_utc()
    future = observation(runtime, str((now + timedelta(seconds=1)).timestamp()))
    dependent = observation(runtime, None, selected=False)
    assert assess(future, config_policy(), now, "source.a").status == "unknown"
    assert assess(dependent, liveness_policy(), now, "source.b", future).status == "unknown"
    old = observation(runtime, str(now.timestamp()))
    runtime.clock.advance(61)
    replay = observation(runtime, str(now.timestamp()))
    assert replay.ha_last_reported > old.ha_last_reported and replay.measurement == old.measurement
    assert assess(replay, config_policy(), runtime.clock.now_utc(), "source.a").status == "unknown"
    assert (
        assess(
            observation(runtime, None, selected=False),
            liveness_policy(),
            runtime.clock.now_utc(),
            "source.b",
            replay,
        ).status
        == "unknown"
    )


async def test_iso_and_unconfigured_binding_keep_existing_time_basis(runtime, config):
    await activate(runtime, config)
    stamp = runtime.clock.now_utc().isoformat()
    assert observation(runtime, stamp).measurement == runtime.clock.now_utc()
    unconfigured = observation(runtime, None, selected=False)
    assert (
        not unconfigured.invalid_timestamp
        and unconfigured.measurement == unconfigured.ha_last_reported
    )
    unavailable = observation(runtime, None, state="unavailable")
    assert not unavailable.invalid_timestamp
    assert (
        assess(unavailable, config_policy(), runtime.clock.now_utc(), "source.a").reasons[0].code
        == "input_unavailable"
    )
