"""Portable media definitions through the real Runtime/Processor boundary."""

import copy
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.evidence import Observation
from core_contracts.freshness_templates import upgrade_document
from core_contracts.manual_source import ManualEvidence
from core_contracts.quality import FieldValue
from core_contracts.registry import fingerprints, validate
from core_contracts.resolver import first_match
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry

from .conftest import activate
from .test_beta_sleep import FLOW
from .test_beta_sleep import command as sleep_command
from .test_beta_sleep import configuration as sleep_config

ROOT = Path(__file__).parents[1]
TEMPLATES = json.loads((ROOT / "examples/media-templates-v1.json").read_text(encoding="utf-8"))
CATALOGS = json.loads((ROOT / "examples/media-catalogs-v1.json").read_text(encoding="utf-8"))
PARAMETERS = {
    "device.pc_state": dict(host_grace_s=10, power_on_w=100, power_off_w=10),
    "media.tv_source": dict(source_grace_s=5),
    "media.private_time": dict(private_end_grace_s=10),
}


def source(name, schema):
    return dict(
        source_id="source." + name,
        source_type={"bool": "boolean", "number": "numeric", "text": "text"}.get(
            schema["type"], "state"
        ),
        source_origin="local",
        origin="manual",
        dependency_id="dep.manual",
        physical_source_key="synthetic." + name,
        value_schema=schema,
        freshness=dict(mode="event_stateful", future_tolerance_s=0),
    )


def configuration(config, type_id):
    doc = copy.deepcopy(config)
    definition = next(d for d in TEMPLATES if d["type_id"] == type_id)
    doc.update(
        config_schema_version=3,
        fingerprint_version=3,
        dependencies=[dict(dependency_id="dep.manual", kind="manual", monitor="internal")],
        definitions=[definition],
        catalogs=CATALOGS,
    )
    doc["sources"] = [source(i["name"], i["schema"]) for i in definition["inputs"]]
    doc["bindings"] = [
        dict(
            binding_id="binding." + i["name"],
            source_id="source." + i["name"],
            adapter=dict(kind="manual"),
        )
        for i in definition["inputs"]
    ]
    doc["contracts"] = [
        dict(
            contract_id="fixture.media",
            type_id=type_id,
            type_version=1,
            enabled=True,
            parameters=PARAMETERS.get(type_id, {}),
            inputs=[dict(name=i["name"], ref="source." + i["name"]) for i in definition["inputs"]],
        )
    ]
    return upgrade_document(doc)


async def observe(runtime, name, value, *, kind="live_change", retained=False, restored=False):
    runtime.clock.advance(0.01)
    now = runtime.clock.now_utc()
    await runtime.submit(
        "observation",
        Observation(
            source_id="source." + name,
            binding_id="binding." + name,
            adapter="manual",
            manual=ManualEvidence(
                request_id=str(uuid4()),
                source_fingerprint=fingerprints(runtime.config, runtime.types)["source." + name],
                recorded_at=now,
                valid_from=now,
                actor={"kind": "synthetic"},
            ),
            value_raw=value,
            ha_time_fired=now,
            received_at=now,
            observation_kind=kind,
            mqtt_retained=retained,
            ha_restored=restored,
            epoch_id=runtime.state.epoch_id,
            ingest_seq=runtime.state.generation + 1,
            registry_revision=runtime.state.active_revision,
            reference_version=2,
        ),
    )


def field(runtime, name="value", contract="fixture.media"):
    return runtime.snapshot()["contracts"][contract]["fields"][name]


async def command(runtime, name, contract="fixture.media", identity=None):
    now = runtime.clock.now_utc()
    return await runtime.submit(
        "command",
        dict(
            command_id=identity or str(uuid4()),
            contract_id=contract,
            command=name,
            args={},
            issued_at=now.isoformat(),
            valid_until=(now + timedelta(seconds=30)).isoformat(),
            expected_registry_revision=runtime.state.active_revision,
            origin=dict(kind="synthetic", actor="synthetic", client_name="synthetic"),
        ),
    )


def test_all_portable_definitions_and_selected_non_applicability():
    for definition in TEMPLATES:
        check_definition(DeclarativeDefinition.model_validate(definition))
    encoded = json.dumps(TEMPLATES)
    assert "ha_entity_id" not in encoded and "mqtt_topic" not in encoded
    yes = FieldValue(status="valid", quality="healthy", value=True)
    absent = FieldValue(status="not_applicable", quality="healthy", positively_not_applicable=True)
    from .test_core import NOW

    assert first_match([(yes, absent)], NOW).status == "not_applicable"
    assert (
        first_match([(yes.model_copy(update={"quality": "degraded"}), absent)], NOW).status
        == "unknown"
    )
    assert (
        first_match([(yes, absent.model_copy(update={"valid_until": NOW}))], NOW).status
        == "unknown"
    )
    marked = yes.model_copy(update={"evidence": ("synthetic-condition",)})
    selected = first_match([(marked, absent)], NOW)
    assert selected.evidence == ("synthetic-condition",)


def test_all_templates_are_reproducible_and_valid_with_catalogs(config):
    from dev.build_media_templates import build, catalogs

    assert build() == TEMPLATES
    assert catalogs() == CATALOGS
    for item in TEMPLATES:
        validate(configuration(config, item["type_id"]), type_registry())


async def test_ps5_menu_requires_positive_no_session_and_title_requires_applicability(
    runtime, config
):
    await activity_ready(runtime, config)
    await observe(runtime, "device", "ps5")
    await observe(runtime, "tv", True)
    assert field(runtime)["value"] == "ps5_menu"
    await observe(runtime, "ps5_session", None)
    assert field(runtime)["status"] == "unknown"
    await observe(runtime, "ps5_session", True)
    await observe(runtime, "ps5_title", "Synthetic Game")
    assert field(runtime)["value"] == "gaming"
    await observe(runtime, "ps5_title", None)
    assert field(runtime)["status"] == "unknown"


@pytest.mark.parametrize(
    "type_id,default",
    [
        ("media.homepods_class", "normal"),
        ("media.ps5_class", "heimkino"),
        ("media.pc_class", "headset"),
        ("media.stash_class", "privat"),
        ("media.appletv_class", "normal"),
    ],
)
async def test_classes_non_applicability_unknown_and_catalog_default(
    runtime, config, type_id, default
):
    await activate(runtime, configuration(config, type_id))
    await observe(runtime, "applicable", False)
    assert field(runtime)["status"] == "not_applicable"
    await observe(runtime, "applicable", True)
    assert field(runtime)["status"] == "unknown"
    if type_id == "media.homepods_class":
        await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Title")
    assert field(runtime)["value"] == default
    assert any(
        r["code"] == "catalog_assignment_missing" for r in field(runtime)["configuration"]["hints"]
    )
    await observe(runtime, "applicable", None)
    assert field(runtime)["status"] in {"unknown", "unresolved"}
    assert field(runtime)["value"] is None


async def test_unknown_discord_does_not_prove_no_pc_title_session(runtime, config):
    await activate(runtime, configuration(config, "media.pc_session"))
    await observe(runtime, "pc", True)
    await observe(runtime, "discord", None)
    assert field(runtime)["status"] == "unknown"
    await observe(runtime, "discord", "idle")
    assert field(runtime)["value"] is False


async def test_pc_hysteresis_host_priority_grace_and_explicit_long_gap(runtime, config):
    await activate(runtime, configuration(config, "device.pc_state"))
    await observe(runtime, "watts", 15)
    assert field(runtime)["status"] == "unknown"
    for watts, expected in [(100, True), (10, True), (9, False), (10, False), (100, True)]:
        await observe(runtime, "watts", watts)
        assert field(runtime)["value"] is expected
    await observe(runtime, "watts", 15)
    await observe(runtime, "host", False)
    assert field(runtime)["value"] is False
    await observe(runtime, "host", None)
    runtime.clock.advance(6)
    await runtime.submit("tick", None)
    assert field(runtime)["status"] == "held" and field(runtime)["value"] is False
    await observe(runtime, "host", False)
    assert field(runtime)["value"] is False
    await observe(runtime, "host", None)
    runtime.clock.advance(11)
    await runtime.submit("tick", None)
    assert field(runtime)["value"] is True
    assert field(runtime)["quality"] == "degraded"
    assert any(r["code"] == "partial_evidence" for r in field(runtime)["reasons"])
    await observe(runtime, "host", False)
    await observe(runtime, "watts", 9)
    assert field(runtime)["value"] is False


async def test_real_sleep_consumer_keeps_sleep_during_six_second_host_gap(runtime, config):
    doc = sleep_config(config)
    pc = configuration(config, "device.pc_state")
    doc["definitions"] += pc["definitions"]
    doc["sources"] += pc["sources"]
    doc["bindings"] += pc["bindings"]
    doc["contracts"] += [{**pc["contracts"][0], "contract_id": "device.pc.power"}]
    flow = doc["contracts"][0]
    next(i for i in flow["inputs"] if i["name"] == "pc")["ref"] = "device.pc.power.value"
    await activate(runtime, doc)
    await observe(runtime, "host", True)
    await observe(runtime, "watts", 100)
    await observe(runtime, "watts", 15)
    await observe(runtime, "host", False)
    await sleep_command(runtime, "confirm_sleep")
    before = copy.deepcopy(runtime.state.tables["node_state"]["fixture.sleep"]["machine"])
    await observe(runtime, "host", None)
    runtime.clock.advance(6)
    await runtime.submit("tick", None)
    await observe(runtime, "host", False)
    after = runtime.state.tables["node_state"]["fixture.sleep"]["machine"]
    assert after["state"] == "sleep"
    assert after["since_at"] == before["since_at"]
    assert after["episode_id"] == before["episode_id"]
    assert next(d for d in doc["definitions"] if d["type_id"] == "sleep.flow") == FLOW


async def test_tv_source_grace_expires_off_clears_and_same_connected_value_does_not_age(
    runtime, config
):
    await activate(runtime, configuration(config, "media.tv_source"))
    await observe(runtime, "tv", True)
    await observe(runtime, "source", "Synthetic Source")
    runtime.clock.advance(10000)
    await runtime.submit("tick", None)
    assert field(runtime)["value"] == "Synthetic Source"
    await observe(runtime, "source", None)
    assert field(runtime)["status"] == "held"
    runtime.clock.advance(5)
    await runtime.submit("tick", None)
    assert field(runtime)["status"] not in {"held", "valid"}

    await observe(runtime, "source", "Synthetic Source")
    await observe(runtime, "source", None)
    await observe(runtime, "tv", False)
    assert field(runtime)["status"] == "not_applicable"
    await observe(runtime, "tv", True)
    assert field(runtime)["status"] not in {"held", "valid"}


def test_tv_source_zero_is_rejected_by_existing_positive_duration_contract(config):
    from core_contracts.validation import RegistryValidationError

    doc = configuration(config, "media.tv_source")
    doc["contracts"][0]["parameters"]["source_grace_s"] = 0
    with pytest.raises(RegistryValidationError):
        validate(doc, type_registry())


async def activity_ready(runtime, config, both=False):
    await activate(runtime, configuration(config, "media.activity_flow"))
    for name, value in [
        ("tv", both),
        ("pc", both),
        ("ps5", False),
        ("appletv", False),
        ("ps5_session", False),
        ("ps5_title", ""),
        ("discord", None),
        ("device", "sonstige"),
        ("usage", "tv"),
    ]:
        await observe(runtime, name, value, kind="snapshot")


async def test_activity_last_activation_fallback_gap_and_discord_unknown_is_pc(runtime, config):
    await activity_ready(runtime, config)
    for name, value, expected in [
        ("tv", True, "tv"),
        ("pc", True, "pc"),
        ("ps5", True, "tv"),
        ("tv", False, "pc"),
        ("tv", True, "tv"),
        ("pc", False, "tv"),
        ("pc", True, "pc"),
        ("appletv", True, "tv"),
    ]:
        await observe(runtime, name, value)
        assert field(runtime)["value"] == expected
    await observe(runtime, "pc", None)
    await observe(runtime, "pc", True)
    assert field(runtime)["value"] == "tv"
    await observe(runtime, "tv", False)
    assert field(runtime)["value"] == "pc"
    await observe(runtime, "discord", "playing")
    assert field(runtime)["value"] == "gaming"
    await observe(runtime, "discord", None)
    assert field(runtime)["value"] == "pc"
    await observe(runtime, "pc", None)
    assert field(runtime)["status"] in {"unknown", "unresolved"}


async def test_activity_both_at_start_is_unresolved_not_tv_priority(runtime, config):
    await activity_ready(runtime, config, both=True)
    assert field(runtime)["value"] == "unresolved"
    await observe(runtime, "ps5", True, kind="snapshot")
    assert field(runtime)["value"] == "unresolved"
    await observe(runtime, "ps5", False)
    await observe(runtime, "ps5", True)
    assert field(runtime)["value"] == "tv"


@pytest.mark.parametrize(
    "tv,device,playing,expected",
    [
        (True, "ps5", None, "ps5"),
        (True, "apple_tv", None, "appletv"),
        (True, "sonstige", None, "keines"),
        (True, None, True, None),
        (False, None, True, "homepods"),
        (False, None, False, "keines"),
        (False, None, None, None),
        (None, "ps5", True, None),
    ],
)
async def test_lead_table(runtime, config, tv, device, playing, expected):
    await activate(runtime, configuration(config, "media.lead_device"))
    for name, value in [("tv", tv), ("device", device), ("playing", playing)]:
        await observe(runtime, name, value)
    assert field(runtime)["value"] == expected


@pytest.mark.parametrize(
    "state,uri,expected",
    [
        ("playing", "library://radio/synthetic", False),
        ("playing", "synthetic://track/example", True),
        ("playing", "synthetic://playlist/example", True),
        ("playing", "https://example.invalid/track/example", None),
        ("playing", None, None),
        ("idle", None, False),
    ],
)
async def test_ma_individual_playback_without_claiming_human_origin(
    runtime, config, state, uri, expected
):
    await activate(runtime, configuration(config, "media.playback_facts"))
    await observe(runtime, "state", state)
    await observe(runtime, "uri", uri)
    assert field(runtime, "manual")["value"] is expected


async def test_private_end_grace_negative_and_unknown_are_distinct(runtime, config):
    await activate(runtime, configuration(config, "media.private_time"))
    await observe(runtime, "appletv", False)
    await observe(runtime, "streams", 1)
    await observe(runtime, "streams", 0)
    assert field(runtime)["status"] == "held" and field(runtime)["value"] is True
    runtime.clock.advance(10)
    await runtime.submit("tick", None)
    assert field(runtime)["value"] is False
    await observe(runtime, "streams", 1)
    await observe(runtime, "streams", None)
    assert field(runtime)["status"] == "held"
    runtime.clock.advance(10)
    await runtime.submit("tick", None)
    assert field(runtime)["status"] == "unknown"


async def test_commands_restart_after_sleep_without_manual_bool_source(runtime, config):
    await activate(runtime, configuration(config, "presence.command_context"))
    await observe(runtime, "sleep", "awake")
    identity = str(uuid4())
    assert (await command(runtime, "start", identity=identity))["status"] == "accepted"
    assert field(runtime)["value"] is True
    assert (await command(runtime, "start", identity=identity))["status"] == "accepted"
    await observe(runtime, "sleep", None)
    assert field(runtime)["value"] is True
    assert field(runtime)["quality"] == "degraded"
    assert any(r["code"] == "partial_evidence" for r in field(runtime)["reasons"])
    await observe(runtime, "sleep", "sleep")
    assert field(runtime)["value"] is False
    await observe(runtime, "sleep", "awake")
    assert field(runtime)["value"] is False
    await command(runtime, "start")
    assert field(runtime)["value"] is True
    await command(runtime, "stop")
    assert field(runtime)["value"] is False


async def test_quiet_facts_independent_since_and_unknown_door(runtime, config):
    await activate(runtime, configuration(config, "media.quiet_facts"))
    await observe(runtime, "door", "closed")
    await observe(runtime, "playing", False)
    before = field(runtime, "entry_door_open")["since_at"]
    await observe(runtime, "track_class", "stumm")
    await observe(runtime, "playing", True)
    assert field(runtime, "track_muted")["value"] is True
    assert field(runtime, "entry_door_open")["since_at"] == before
    await observe(runtime, "door", None)
    assert field(runtime, "entry_door_open")["status"] == "unknown"
    assert "active" not in runtime.snapshot()["contracts"]["fixture.media"]["fields"]


async def test_restore_snapshots_never_claim_a_new_activity_activation(runtime, config):
    await activity_ready(runtime, config)
    await observe(runtime, "tv", True)
    await observe(runtime, "pc", True)
    assert field(runtime)["value"] == "pc"
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        for name, value in [
            ("tv", True),
            ("pc", True),
            ("ps5", True),
            ("appletv", True),
            ("device", "sonstige"),
            ("usage", "tv"),
            ("discord", None),
        ]:
            await observe(restored, name, value, kind="snapshot")
        assert field(restored)["value"] in {"pc", "unresolved", None}
        assert field(restored, "branch")["value"] != "tv"
    finally:
        await restored.stop()


async def test_private_end_deadline_is_not_restarted_by_restore(runtime, config):
    await activate(runtime, configuration(config, "media.private_time"))
    await observe(runtime, "appletv", False)
    await observe(runtime, "streams", 1)
    await observe(runtime, "streams", 0)
    runtime.clock.advance(6)
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        await observe(restored, "appletv", False, kind="snapshot")
        await observe(restored, "streams", 0, kind="snapshot")
        runtime.clock.advance(4)
        await restored.submit("tick", None)
        assert field(restored)["value"] is False
    finally:
        await restored.stop()
