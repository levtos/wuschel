"""P8 required probes; synthetic only, execution deferred to the final b22 gate."""

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.calendar_coverage import horizon, status
from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.fact_package import additions_for, assemble, category
from core_contracts.freshness_templates import upgrade_document
from core_contracts.registry import fingerprints
from core_contracts.service_jobs import calendar_status
from core_contracts.validation import validate_document as validate
from dev.build_fact_templates import build
from dev.build_media_templates import definition, inp, literal, schema

from .conftest import activate
from .test_alpha9_calendar import calendar_config, fetch
from .test_beta_media import field, observe, source
from .test_portable import export


def configuration(config, type_id, template=None):
    doc = copy.deepcopy(config)
    template = template or next(row for row in build() if row["type_id"] == type_id)
    doc.update(
        config_schema_version=3,
        fingerprint_version=3,
        catalogs=[],
        definitions=[template],
        dependencies=[{"dependency_id": "dep.manual", "kind": "manual", "monitor": "internal"}],
        sources=[source(item["name"], item["schema"]) for item in template["inputs"]],
        bindings=[
            {
                "binding_id": "binding." + item["name"],
                "source_id": "source." + item["name"],
                "adapter": {"kind": "manual"},
            }
            for item in template["inputs"]
        ],
        contracts=[
            {
                "contract_id": "fixture.fact",
                "type_id": type_id,
                "type_version": template["type_version"],
                "enabled": True,
                "ha_projection": True,
                "parameters": {},
                "inputs": [
                    {"name": item["name"], "ref": "source." + item["name"]}
                    for item in template["inputs"]
                ],
            }
        ],
    )
    return upgrade_document(doc)


def value(runtime, name="value"):
    return field(runtime, name, "fixture.fact")


async def test_private_package_uses_real_console_bool_field_and_schema4_source_rules(
    runtime, config
):
    root = Path(__file__).parents[1]
    console = next(
        row
        for row in json.loads(
            (root / "examples/media-templates-v1.json").read_text(encoding="utf-8")
        )
        if row["type_id"] == "device.console_state"
    )
    doc = copy.deepcopy(config)
    doc.update(config_schema_version=3, fingerprint_version=3, definitions=[console])
    doc["sources"] = [
        {
            **copy.deepcopy(config["sources"][0]),
            "source_id": "source.synthetic." + name,
            "physical_source_key": "physical.synthetic." + name,
            "source_type": "text" if name == "console" else "numeric",
            "value_schema": {
                "type": "text" if name == "console" else "number",
                "required": True,
                "physical_state": False,
                **({"unit": unit} if unit else {}),
            },
        }
        for name, unit in (("console", None), ("watts", "W"), ("distance", "m"))
    ]
    doc["bindings"] = [
        {
            "binding_id": "binding.synthetic." + name,
            "source_id": "source.synthetic." + name,
            "adapter": {"kind": "ha_state", "ha_entity_id": "sensor.synthetic_" + name},
            "transform": {"kind": "type_cast", "target": "text" if name == "console" else "number"},
        }
        for name in ("console", "watts", "distance")
    ]
    doc["contracts"] = [
        {
            "contract_id": "device.synthetic.console",
            "type_id": "device.console_state",
            "type_version": 1,
            "enabled": True,
            "parameters": {},
            "inputs": [{"name": "console", "ref": "source.synthetic.console"}],
        }
    ]
    await activate(runtime, upgrade_document(doc))
    exported = await export(runtime)
    original = copy.deepcopy(exported)
    additions = additions_for(
        exported["config"],
        {
            "battery": "sensor.synthetic_battery",
            "charging": "binary_sensor.synthetic_charging",
            "call": "sensor.synthetic_call",
        },
        "source.synthetic.watts",
        "source.synthetic.distance",
    )
    prepared = assemble(exported, build(), [], additions)
    parsed = validate(prepared["config"], runtime.types)
    assert exported == original
    consumer = next(row for row in parsed.contracts if row.contract_id == "media.console.state")
    assert (
        next(item.ref for item in consumer.inputs if item.name == "active")
        == "device.synthetic.console.active"
    )
    authored = next(
        row for row in prepared["config"]["sources"] if row["source_id"] == "source.tablet.charging"
    )
    assert "freshness" not in authored
    assert authored["freshness_selection"] == {
        "kind": "own",
        "rule": {"mode": "event_stateful", "future_tolerance_s": 0},
    }
    # The ordinary validator must still reject the original enum-to-bool error.
    invalid = copy.deepcopy(prepared["config"])
    target = next(
        row for row in invalid["contracts"] if row["contract_id"] == "media.console.state"
    )
    next(item for item in target["inputs"] if item["name"] == "active")["ref"] = (
        "device.synthetic.console.value"
    )
    with pytest.raises(ValueError):
        validate(invalid, runtime.types)
    assert (
        category({"contract_id": "opening_zone.synthetic", "type_id": "opening_zone.facts"})
        == "openings"
    )


async def test_tv_usage_latest_v3_off_is_positively_not_applicable_with_unknown_source(
    runtime, config
):
    root = Path(__file__).parents[1]
    template = next(
        row
        for row in json.loads(
            (root / "examples/interface-templates-b22.json").read_text(encoding="utf-8")
        )
        if row["type_id"] == "media.tv_usage"
    )
    assert template["type_version"] == 3
    doc = configuration(config, "media.tv_usage", template)
    doc["catalogs"] = json.loads(
        (root / "examples/interface-catalogs-b22.json").read_text(encoding="utf-8")
    )
    await activate(runtime, doc)
    await observe(runtime, "applicable", False)
    assert value(runtime)["status"] == "not_applicable"
    assert value(runtime)["quality"] == "healthy"
    assert value(runtime)["value"] is None
    assert runtime.snapshot()["contracts"]["fixture.fact"]["contract_health"] == "healthy"


def test_fact_definitions_are_portable_and_well_typed():
    for template in build():
        check_definition(DeclarativeDefinition.model_validate(template))
        assert "ha_entity_id" not in str(template) and "mqtt_topic" not in str(template)


async def test_pc_power_exact_thresholds_hysteresis_host_independence_and_unknown(runtime, config):
    doc = configuration(config, "device.pc_power")
    # An independent host source is deliberately never wired into the power fact.
    doc["sources"].append(copy.deepcopy(doc["sources"][0]))
    doc["sources"][-1].update(
        source_id="source.host",
        source_type="boolean",
        physical_source_key="synthetic.host",
        value_schema=schema(),
    )
    doc["bindings"].append(
        {"binding_id": "binding.host", "source_id": "source.host", "adapter": {"kind": "manual"}}
    )
    await activate(runtime, doc)
    await observe(runtime, "host", False)
    await observe(runtime, "watts", 5)
    since_off = value(runtime)["since_at"]
    await observe(runtime, "watts", 19)
    assert value(runtime)["value"] is False and value(runtime)["since_at"] == since_off
    await observe(runtime, "watts", 20)
    assert value(runtime)["value"] is True
    since_on = value(runtime)["since_at"]
    for watts in (21, 7, 6, 50):
        await observe(runtime, "watts", watts)
        assert value(runtime)["value"] is True and value(runtime)["since_at"] == since_on
    await observe(runtime, "watts", 5)
    assert value(runtime)["value"] is False and value(runtime)["since_at"] != since_off
    await observe(runtime, "watts", None)
    assert value(runtime)["status"] == "unknown" and value(runtime)["value"] is None
    # Unknown clears the volatile latch: an ambiguous restart value stays unknown.
    await observe(runtime, "watts", 19)
    assert value(runtime)["status"] == "unknown"


async def test_tablet_charging_has_independent_since_and_battery_keeps_percent(runtime, config):
    await activate(runtime, configuration(config, "device.charging"))
    await observe(runtime, "charging", False)
    before = value(runtime)["since_at"]
    await observe(runtime, "charging", False)
    assert value(runtime)["since_at"] == before
    await observe(runtime, "charging", True)
    assert value(runtime)["value"] is True and value(runtime)["since_at"] != before
    on_since = value(runtime)["since_at"]
    await observe(runtime, "charging", False)
    assert value(runtime)["value"] is False and value(runtime)["since_at"] != on_since
    await activate(runtime, configuration(config, "device.battery"))
    await observe(runtime, "level", 47)
    assert value(runtime)["value"] == 47 and value(runtime)["since_at"]


async def test_call_idle_dialing_talking_ringing_and_unknown(runtime, config):
    await activate(runtime, configuration(config, "communication.call_active"))
    await observe(runtime, "state", "idle")
    assert value(runtime)["value"] is False
    await observe(runtime, "state", "dialing")
    started = value(runtime)["since_at"]
    for state in ("talking", "ringing"):
        await observe(runtime, "state", state)
        assert value(runtime)["value"] is True and value(runtime)["since_at"] == started
    await observe(runtime, "state", "idle")
    assert value(runtime)["value"] is False
    await observe(runtime, "state", "unsupported")
    assert value(runtime)["status"] == "unknown"


async def test_distance_is_public_meters_and_media_state_uses_field_since(runtime, config):
    await activate(runtime, configuration(config, "presence.distance"))
    await observe(runtime, "meters", 123.5)
    assert value(runtime)["value"] == 123.5
    assert runtime.config.definitions[0].output_schema.fields["value"].unit == "m"
    await activate(runtime, configuration(config, "media.device_state"))
    for name, raw in (
        ("state", "playing"),
        ("title", "Synthetic item A"),
        ("artist", "Synthetic author"),
    ):
        await observe(runtime, name, raw)
    since = value(runtime)["since_at"]
    await observe(runtime, "title", "Synthetic item B")
    assert value(runtime)["since_at"] == since
    assert value(runtime, "title")["value"] == "Synthetic item B"
    await observe(runtime, "state", "paused")
    assert value(runtime)["value"] == "paused" and value(runtime)["since_at"] != since


async def test_same_ids_survive_rename_and_rebinding_without_old_evidence(runtime, config):
    await activate(runtime, config)
    adapter = BridgeAdapter(runtime, None, "ws://unused", "synthetic")

    async def report(raw):
        runtime.clock.advance(1)
        stamp = runtime.clock.now_utc().isoformat()
        item = adapter.observation(
            runtime.config.bindings[0],
            {
                "state": raw,
                "attributes": {},
                "last_changed": stamp,
                "last_updated": stamp,
                "last_reported": stamp,
            },
            "report",
            {"time_fired": stamp},
            runtime.config,
        )
        await runtime.submit("observation", item)

    await report("on")
    baseline = fingerprints(runtime.config, runtime.types)
    before = field(runtime, contract="fixture.echo")["since_at"]
    renamed = copy.deepcopy(config)
    renamed["sources"][0]["display_name"] = "Synthetischer neuer Name"
    renamed["contracts"][0]["display_name"] = "Synthetischer neuer Vertragsname"
    await activate(runtime, renamed)
    assert fingerprints(runtime.config, runtime.types) == baseline
    assert field(runtime, contract="fixture.echo")["since_at"] == before
    replacement = copy.deepcopy(renamed)
    replacement["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.synthetic_replacement"
    await activate(runtime, replacement)
    assert field(runtime, contract="fixture.echo")["status"] == "unknown"
    await report("off")
    assert field(runtime, contract="fixture.echo")["value"] is False
    assert [row.source_id for row in runtime.config.sources] == ["source.a"]
    assert [row.binding_id for row in runtime.config.bindings] == ["binding.a"]
    assert [row.contract_id for row in runtime.config.contracts] == ["fixture.echo"]


def holiday_config(config):
    doc = calendar_config(config)
    template = definition(
        "fixture.holiday",
        {"calendar": schema("object")},
        {"value": {"op": "calendar_active", "arg": inp("calendar"), "pattern": literal("*")}},
        {"value": schema()},
    )
    doc.update(config_schema_version=3, fingerprint_version=3, definitions=[template])
    doc["sources"][0]["calendar_coverage_months"] = 12
    doc["contracts"][0].update(
        type_id="fixture.holiday", inputs=[{"name": "calendar", "ref": "source.a"}]
    )
    return doc


async def sweep(runtime, appointment=None, outcome="completed"):
    for _ in range(30):
        job = await fetch(
            runtime,
            {
                "calendar.example": {
                    "events": appointment
                    if isinstance(appointment, list)
                    else [appointment]
                    if appointment
                    else []
                }
            },
            outcome=outcome,
        )
        coverage = calendar_status(runtime.state, runtime.config, runtime.clock.now_utc())[0][
            "coverage"
        ]
        if coverage["complete"] or outcome != "completed":
            return job
    raise AssertionError("bounded coverage sweep did not finish")


def regular_coverage(runtime, first):
    end = horizon(runtime.clock.now_utc(), 12)[1]
    result = []
    while first < end:
        result.append(
            {
                "summary": "Synthetic holiday",
                "start": first.isoformat(),
                "end": (first + timedelta(hours=1)).isoformat(),
            }
        )
        first += timedelta(days=90)
    return result


async def test_holiday_13_month_old_end_is_visible_and_unknown_until_future_coverage_returns(
    runtime, config
):
    await activate(runtime, holiday_config(config))
    old = runtime.clock.now_utc() - timedelta(days=400)
    runtime.state = runtime.state.clone()  # Own this synthetic historical evidence.
    runtime.state.tables["service_state"]["calendar:binding.a"]["last_event_end"] = old.isoformat()
    await sweep(runtime)
    checked = calendar_status(runtime.state, runtime.config, runtime.clock.now_utc())[0]["coverage"]
    assert not checked["covered"] and old.strftime("%d.%m.%Y") in checked["message"]
    failed = field(runtime, contract="fixture.echo")
    assert failed["value"] is None and failed["quality"] == "degraded"
    assert failed["reasons"][0]["code"] == "calendar_coverage_missing"
    runtime.clock.advance(901)
    await runtime.submit("tick", {})
    tomorrow = runtime.clock.now_utc() + timedelta(days=1)
    await sweep(
        runtime,
        regular_coverage(runtime, tomorrow),
    )
    assert field(runtime, contract="fixture.echo")["quality"] == "healthy"
    assert (
        calendar_status(runtime.state, runtime.config, runtime.clock.now_utc())[0]["coverage"][
            "message"
        ]
        is None
    )


async def test_regular_three_month_gap_does_not_warn_and_probe_never_expands_fact_cache(
    runtime, config
):
    await activate(runtime, holiday_config(config))
    upcoming = runtime.clock.now_utc() + timedelta(days=90)
    await sweep(
        runtime,
        regular_coverage(runtime, upcoming),
    )
    assert field(runtime, contract="fixture.echo")["value"] is False
    assert field(runtime, contract="fixture.echo")["quality"] == "healthy"
    assert runtime.state.tables["service_state"]["calendar:binding.a"]["events"] == []
    jobs = [job for job in runtime.state.tables["service_job"].values() if job.get("coverage")]
    from datetime import datetime

    assert all(
        datetime.fromisoformat(job["arguments"]["end"])
        - datetime.fromisoformat(job["arguments"]["start"])
        <= timedelta(days=31, hours=1)
        for job in jobs
    )


async def test_coverage_error_expiry_and_rebinding_never_reuse_previous_proof(runtime, config):
    doc = holiday_config(config)
    await activate(runtime, doc)
    now = runtime.clock.now_utc()
    tomorrow = now + timedelta(days=1)
    await sweep(
        runtime,
        regular_coverage(runtime, tomorrow),
    )
    assert status(runtime.state, runtime.config.bindings[0], 12, now)["covered"]
    runtime.clock.advance(3601)
    await runtime.submit("tick", {})
    await sweep(runtime, outcome="failed")
    assert field(runtime, contract="fixture.echo")["quality"] == "degraded"
    doc["bindings"][0]["adapter"]["ha_entity_id"] = "calendar.synthetic_replacement"
    await activate(runtime, doc)
    assert not status(runtime.state, runtime.config.bindings[0], 12, runtime.clock.now_utc())[
        "covered"
    ]


def test_configurable_calendar_months_handles_leap_day_and_rejects_non_calendar(config):
    from datetime import UTC, datetime

    from core_contracts.registry import RegistryConfig

    assert horizon(datetime(2024, 2, 29, tzinfo=UTC), 12)[1].day == 28
    config["sources"][0]["calendar_coverage_months"] = 12
    with pytest.raises(ValueError, match="calendar_coverage_requires_calendar"):
        RegistryConfig.model_validate(config)
