import copy
from datetime import timedelta

import pytest

from core_contracts.evidence import Freshness, Observation, assess
from core_contracts.quality import FieldValue, ReasonCode, derive, unknown
from core_contracts.temporal import Temporal, TemporalState
from core_contracts.validation import validate_document as validate
from core_contracts.value_origin import ValueOrigin, forecast_points

from .conftest import activate
from .test_core import NOW
from .test_declarative import definition, observation, registry, schema
from .test_phase1a import add_source


def sensor_config(config, *, reference=False):
    data = definition()
    data["inputs"][0]["schema"] = schema("number", unit="°C")
    data["schema"]["fields"]["value"] = schema("number", unit="°C")
    config = registry(copy.deepcopy(config), data)
    config["config_schema_version"] = 2
    profile = dict(
        id="example.numeric",
        version=1,
        stuck=dict(unchanged_for_s=60, tolerance=0.05, min_reports=3, recovery_reports=2),
    )
    config["sources"][0].update(
        sensor_profile=profile,
        freshness=dict(mode="periodic_ttl", interval_s=120, future_tolerance_s=1),
    )
    if reference:
        add_source(config, "b")
        config["sources"][1].pop("sensor_profile")
        profile["stuck"]["reference"] = dict(ref="source.b", min_delta=2)
    return config


def field(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]


async def report(runtime, value=20, source="a"):
    await runtime.submit(
        "observation",
        observation(runtime, value, source).model_copy(
            update={
                "ingest_seq": runtime.state.generation + 1,
                "observation_kind": "report",
                "ha_last_reported": runtime.clock.now_utc(),
            }
        ),
    )


async def test_sensor_needs_reports_and_duration_then_recovers_without_replacing_value(
    runtime, config
):
    await activate(runtime, sensor_config(config))
    await report(runtime)
    runtime.clock.advance(30)
    await report(runtime, 20.02)
    runtime.clock.advance(30)
    await report(runtime, 20.01)
    assert field(runtime)["status"] == "valid" and field(runtime)["quality"] == "degraded"
    assert field(runtime)["value"] == 20.01 and field(runtime)["plausibility"] == "suspicious"
    assert field(runtime)["reasons"][0]["code"] == "sensor_stuck_suspected"
    runtime.clock.advance(1)
    await report(runtime, 21)
    assert field(runtime)["plausibility"] == "suspicious"
    runtime.clock.advance(1)
    await report(runtime, 21.1)
    assert field(runtime)["quality"] == "healthy" and field(runtime)["plausibility"] == "plausible"


async def test_snapshot_duplicates_restart_and_gaps_do_not_prove_constant_measurements(
    runtime, config
):
    await activate(runtime, sensor_config(config))
    await report(runtime)
    before = runtime.state.tables["source_observation_current"]["binding.a"]
    await runtime.submit("observation", Observation.model_validate(before))
    assert runtime.state.tables["service_state"]["sensor:source.a"]["report_count"] == 1
    runtime.clock.advance(60)
    await runtime.submit("tick")
    assert field(runtime)["quality"] == "healthy"
    await runtime._restore("process_restart")
    assert field(runtime)["plausibility"] == "unchecked"
    await runtime.submit(
        "snapshot", [observation(runtime, 20).model_copy(update={"observation_kind": "snapshot"})]
    )
    assert runtime.state.tables["service_state"]["sensor:source.a"]["report_count"] == 0
    runtime.clock.advance(1)
    await report(runtime)
    assert runtime.state.tables["service_state"]["sensor:source.a"]["report_count"] == 1


async def test_reference_must_show_independent_change_within_the_proven_window(runtime, config):
    await activate(runtime, sensor_config(config, reference=True))
    await report(runtime, 20, "b")
    await report(runtime)
    runtime.clock.advance(30)
    await report(runtime)
    await report(runtime, 21, "b")
    runtime.clock.advance(30)
    await report(runtime)
    assert field(runtime)["quality"] == "healthy"
    await report(runtime, 23, "b")
    assert field(runtime)["plausibility"] == "suspicious"


async def test_profile_version_is_immutable_parameters_are_semantic_and_contacts_have_no_default(
    runtime, config
):
    doc = sensor_config(config)
    await activate(runtime, doc)
    changed = copy.deepcopy(doc)
    changed["sources"][0]["sensor_profile"]["stuck"]["tolerance"] = 0.1
    with pytest.raises(ValueError):
        await runtime.submit("validate", changed)
    changed["sources"][0]["sensor_profile"]["version"] = 2
    await activate(runtime, changed)
    contact = copy.deepcopy(config)
    assert validate(contact, runtime.types).sources[0].sensor_profile is None
    contact["sources"][0]["sensor_profile"] = doc["sources"][0]["sensor_profile"]
    with pytest.raises(ValueError):
        validate(contact, runtime.types)


def point(value, begin=0, end=60, issued=-10):
    return dict(
        value=value,
        value_origin=dict(
            kind="forecast",
            provider="example",
            issued_at=(NOW + timedelta(seconds=issued)).isoformat(),
            valid_from=(NOW + timedelta(seconds=begin)).isoformat(),
            valid_until=(NOW + timedelta(seconds=end)).isoformat(),
        ),
    )


def forecast_observation(points):
    return Observation(
        source_id="source.forecast",
        binding_id="binding.forecast",
        adapter="mqtt",
        value_raw=points,
        value_normalized=points,
        normalized=True,
        forecast=forecast_points(points),
        device_time=NOW,
        received_at=NOW,
        observation_kind="report",
        epoch_id="example",
        ingest_seq=1,
    )


def test_forecast_half_open_selection_gap_overlap_and_provider_freshness():
    obs = forecast_observation([point(1), point(2, begin=120, end=180)])
    policy = Freshness(mode="periodic_ttl", interval_s=300, future_tolerance_s=0)
    assert assess(obs, policy, NOW, obs.source_id).value == 1
    assert assess(obs, policy, NOW + timedelta(seconds=60), obs.source_id).status == "unknown"
    assert assess(obs, policy, NOW + timedelta(seconds=120), obs.source_id).value == 2
    assert assess(obs, policy, NOW + timedelta(seconds=180), obs.source_id).status == "unknown"
    stale = forecast_observation([point(1, issued=-1000)])
    assert assess(stale, policy, NOW, stale.source_id).reasons[0].code == ReasonCode.INPUT_STALE
    with pytest.raises(ValueError, match="overlapping"):
        forecast_points([point(1), point(2, begin=30, end=90)])
    assert len(forecast_points([point(1), point(1, begin=30, end=90)])) == 2


def test_origins_do_not_relabel_calculations_and_validity_cannot_be_extended_by_grace():
    origin = ValueOrigin.model_validate(point(1)["value_origin"])
    value = FieldValue(
        status="valid",
        quality="healthy",
        value=1,
        value_origin=origin,
        valid_from=NOW,
        valid_until=NOW + timedelta(seconds=60),
    )
    measured = value.model_copy(
        update={
            "value_origin": ValueOrigin(kind="measured"),
            "valid_until": NOW + timedelta(seconds=30),
        }
    )
    derived = derive(2, [value, measured], NOW)
    assert derived.value_origin is None and len(derived.input_origins) == 2
    assert derived.valid_until == NOW + timedelta(seconds=30)
    temporal = Temporal(TemporalState(fingerprint="example"))
    temporal.grace(value, 300, NOW, trigger=True)
    held = temporal.grace(
        unknown(ReasonCode.INPUT_STALE, "a", NOW), 300, NOW + timedelta(seconds=10), trigger=True
    )
    assert held.held_until == NOW + timedelta(seconds=60)
    assert (
        temporal.grace(
            unknown(ReasonCode.INPUT_STALE, "a", NOW),
            300,
            NOW + timedelta(seconds=60),
            trigger=True,
        ).status
        == "unknown"
    )


async def test_forecast_boundaries_re_evaluate_through_clock_without_polling(runtime, config):
    from .test_alpha9_manual_source import body, enter, manual_config

    declaration = definition()
    declaration["inputs"][0]["schema"] = schema("number")
    declaration["schema"]["fields"]["value"] = schema("number")
    document = manual_config(config, ttl=300)
    document["definitions"] = [declaration]
    document["sources"][0].update(
        source_type="numeric",
        value_schema=schema("number"),
        value_origin=dict(kind="forecast", provider="example"),
    )
    await activate(runtime, document)
    # Runtime fixture is at NOW; a point's issue time is source time, never receipt.
    from datetime import datetime

    delta = runtime.clock.now_utc() - NOW
    data = point(12)
    data["value_origin"] = {
        key: (datetime.fromisoformat(value) + delta).isoformat()
        if key in {"issued_at", "valid_from", "valid_until"}
        else value
        for key, value in data["value_origin"].items()
    }
    assert (await enter(runtime, {**body(runtime), "value": [data]}))["status"] == "accepted"
    assert field(runtime)["value"] == 12
    runtime.clock.advance(60)
    await runtime.submit("tick")
    assert field(runtime)["status"] == "unknown"


async def test_sensor_parameters_change_baseline_without_changing_profile_definition(
    runtime, config
):
    doc = sensor_config(config)
    profile = doc["sources"][0]["sensor_profile"]
    profile["parameter_schema"] = {"tolerance": {"schema": schema("number"), "minimum": 0}}
    profile["stuck"]["tolerance"] = {"op": "parameter", "name": "tolerance"}
    doc["sources"][0]["sensor_parameters"] = {"tolerance": 0.05}
    await activate(runtime, doc)
    await report(runtime)
    before = copy.deepcopy(runtime.state.tables["service_state"]["sensor:source.a"])
    doc["sources"][0]["sensor_parameters"]["tolerance"] = 0.1
    await activate(runtime, doc)
    after = runtime.state.tables["service_state"].get("sensor:source.a")
    assert after is None or after["report_count"] == 0
    assert (
        runtime._evaluation_plan(runtime.state, runtime.config)[0]["source.a"]
        != before["fingerprint"]
    )
    assert (await runtime.store.sensor_profile(profile["id"], 1))["stuck"]["tolerance"] == {
        "op": "parameter",
        "name": "tolerance",
    }


async def test_sensor_assessment_is_not_published_on_commit_failure(runtime, config):
    await activate(runtime, sensor_config(config))
    await report(runtime)
    before = copy.deepcopy(runtime.state.tables["service_state"])
    runtime.clock.advance(1)
    await runtime.submit("tick")
    runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("failure"))
    try:
        with pytest.raises(OSError):
            await report(runtime)
        assert runtime.state.tables["service_state"] == before
    finally:
        runtime.store.before_commit = None


@pytest.mark.parametrize("kind", ["measured", "replacement", "model", "forecast"])
def test_transport_origin_is_explicit_and_old_sources_are_not_relabelled(config, kind):
    from core_contracts.source_values import normalize
    from core_contracts.testing.contract_types import type_registry

    document = copy.deepcopy(config)
    document["config_schema_version"] = 2
    document["sources"][0]["value_origin"] = {"kind": kind, "provider": "example"}
    cfg = validate(document, type_registry())
    metadata = dict(point(True)["value_origin"], kind=kind)
    number, extra = normalize(True, metadata, cfg.bindings[0], cfg)
    assert number is True and extra["value_origin"].kind == kind
    old = validate(config, type_registry())
    assert normalize(True, metadata, old.bindings[0], old)[1] == {}
