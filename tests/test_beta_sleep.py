"""The imported sleep definition runs through the real transaction/command path."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.evidence import Observation
from core_contracts.freshness_templates import upgrade_document
from core_contracts.manual_source import ManualEvidence
from core_contracts.registry import fingerprints
from core_contracts.wake.settings import WakeSettings
from core_contracts.wake.wake_config import default_config

from .conftest import activate

TEMPLATES = json.loads((Path(__file__).parents[1] / "examples/sleep-templates-v1.json").read_text())
FLOW = next(item for item in TEMPLATES if item["type_id"] == "sleep.flow")


def settings():
    raw = default_config().to_dict()
    for name in ("contract_version", "calendar_entity", "holiday_calendar_entity", "migration"):
        raw.pop(name)
    return WakeSettings.model_validate(raw).model_dump(mode="json")


def configuration(config):
    data = copy.deepcopy(config)
    data["config_schema_version"] = 3
    data["fingerprint_version"] = 3
    data["dependencies"] = [dict(dependency_id="dep.manual", kind="manual", monitor="internal")]
    data["definitions"] = [copy.deepcopy(FLOW)]
    data["sources"], data["bindings"] = [], []
    for item in FLOW["inputs"]:
        name, schema = item["name"], item["schema"]
        data["sources"].append(
            dict(
                source_id="source." + name,
                source_type="numeric"
                if schema["type"] == "number"
                else "boolean"
                if schema["type"] == "bool"
                else "text"
                if schema["type"] == "text"
                else "state",
                source_origin="local",
                origin="manual",
                dependency_id="dep.manual",
                physical_source_key="synthetic." + name,
                value_schema=schema,
                freshness=dict(mode="event_stateful", future_tolerance_s=0),
            )
        )
        data["bindings"].append(
            dict(
                binding_id="binding." + name,
                source_id="source." + name,
                adapter=dict(kind="manual"),
            )
        )
        if name == "manual_exception":
            data["sources"][-1]["manual_structured_schema"] = "wake_exception_v1"
    data["contracts"] = [
        dict(
            contract_id="fixture.sleep",
            type_id="sleep.flow",
            type_version=1,
            enabled=True,
            parameters={"wake_settings": settings()},
            inputs=[dict(name=i["name"], ref="source." + i["name"]) for i in FLOW["inputs"]],
        )
    ]
    return upgrade_document(data)


def machine(runtime):
    return runtime.state.tables["node_state"]["fixture.sleep"]["machine"]


def fields(runtime):
    return runtime.state.tables["contract_state_current"]["fixture.sleep"]["fields"]


async def observe(runtime, name, value, *, kind="live_change", retained=False, restored=False):
    await runtime.submit(
        "observation",
        Observation(
            source_id="source." + name,
            binding_id="binding." + name,
            adapter="manual",
            manual=ManualEvidence(
                request_id=str(uuid4()),
                source_fingerprint=fingerprints(runtime.config, runtime.types)["source." + name],
                recorded_at=runtime.clock.now_utc(),
                valid_from=runtime.clock.now_utc(),
                actor={"kind": "synthetic"},
            ),
            value_raw=value,
            ha_time_fired=runtime.clock.now_utc(),
            received_at=runtime.clock.now_utc(),
            observation_kind=kind,
            mqtt_retained=retained,
            ha_restored=restored,
            epoch_id=runtime.state.epoch_id,
            ingest_seq=runtime.state.generation + 1,
            registry_revision=runtime.state.active_revision,
            reference_version=2,
        ),
    )


async def command(runtime, name, identity=None):
    now = runtime.clock.now_utc()
    return await runtime.submit(
        "command",
        dict(
            command_id=identity or str(uuid4()),
            contract_id="fixture.sleep",
            command=name,
            args={},
            issued_at=now.isoformat(),
            valid_until=(now + timedelta(seconds=30)).isoformat(),
            expected_registry_revision=runtime.state.active_revision,
            origin=dict(kind="synthetic", actor="synthetic", client_name="synthetic"),
        ),
    )


async def ready(runtime, config, wake_time=None, minimum=None, reverse_outputs=False):
    document = configuration(config)
    if reverse_outputs:
        document["definitions"][0]["outputs"] = dict(
            reversed(document["definitions"][0]["outputs"].items())
        )
    if wake_time:
        for profile in document["contracts"][0]["parameters"]["wake_settings"]["profiles"].values():
            profile["wake_time"] = wake_time
            profile["minimum_sleep_minutes"] = minimum
    await activate(runtime, document)
    for name, value in (
        ("tv", False),
        ("pc", False),
        ("location", "home"),
        ("permission", False),
        ("coffee", False),
        ("hallway", False),
        ("terrace", "closed"),
        ("window_1", "closed"),
        ("volume", 10),
    ):
        await observe(runtime, name, value, kind="snapshot")
    now = runtime.clock.now_utc()
    day = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    cache = dict(
        window_start=(now - timedelta(days=1)).isoformat(),
        window_end=(now + timedelta(days=32)).isoformat(),
        fetched_at=now.isoformat(),
        expires_at=(now + timedelta(days=2)).isoformat(),
        events=[],
    )
    await observe(runtime, "local_date", day.isoformat(), kind="snapshot")
    await observe(runtime, "appointments", cache, kind="snapshot")
    await observe(runtime, "holiday_days", cache, kind="snapshot")


def test_all_portable_definitions_typecheck_and_no_private_addresses():
    for data in TEMPLATES:
        check_definition(DeclarativeDefinition.model_validate(data))
    encoded = json.dumps(TEMPLATES)
    assert "ha_entity_id" not in encoded and "mqtt_topic" not in encoded


async def test_commands_reset_repeatedly_without_new_episode_or_state_begin(runtime, config):
    await ready(runtime, config)
    assert (await command(runtime, "start_pre_sleep"))["status"] == "accepted"
    before = copy.deepcopy(machine(runtime))
    runtime.clock.advance(60)
    assert (await command(runtime, "start_pre_sleep"))["status"] == "accepted"
    after = machine(runtime)
    assert datetime.fromisoformat(after["deadline"]["at"]) == runtime.clock.now_utc() + timedelta(
        minutes=45
    )
    for key in ("since_at", "episode_id", "episode_started_at", "entry_label"):
        assert after[key] == before[key]
    runtime.clock.advance(1)
    assert (await command(runtime, "start_pre_sleep"))["status"] == "accepted"
    assert machine(runtime)["deadline"]["deadline_id"] != after["deadline"]["deadline_id"]
    assert (await command(runtime, "confirm_sleep"))["status"] == "accepted"
    assert machine(runtime)["entry_label"] == "confirmed"
    assert machine(runtime)["episode_id"] == before["episode_id"]
    assert "first_sleep_at" in machine(runtime)["captures"]


async def test_timeout_clears_sleep_deadline_and_tv_off_does_not_change_it(runtime, config):
    await ready(runtime, config)
    await command(runtime, "start_pre_sleep")
    before = copy.deepcopy(machine(runtime))
    await observe(runtime, "tv", True)
    runtime.clock.advance(1)
    await observe(runtime, "tv", False)
    assert machine(runtime)["deadline"] == before["deadline"]
    runtime.clock.advance(2699)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "sleep"
    assert machine(runtime)["deadline"] is None
    assert machine(runtime)["entry_label"] == "timeout"
    assert machine(runtime)["episode_id"] == before["episode_id"]


async def test_K1_unknown_location_does_not_block_interaction_or_wake(runtime, config):
    await ready(runtime, config)
    await command(runtime, "start_pre_sleep")
    await observe(runtime, "location", None)
    runtime.clock.advance(60)
    await observe(runtime, "hallway", True)
    assert datetime.fromisoformat(
        machine(runtime)["deadline"]["at"]
    ) == runtime.clock.now_utc() + timedelta(minutes=45)
    await command(runtime, "confirm_sleep")
    runtime.clock.advance(1)
    await observe(runtime, "coffee", True)
    assert machine(runtime)["state"] == "awake"


@pytest.mark.parametrize("missing", ["tv", "pc"])
async def test_K2_only_pre_sleep_holds_on_missing_device(runtime, config, missing):
    await ready(runtime, config)
    await command(runtime, "start_pre_sleep")
    before = copy.deepcopy(machine(runtime))
    await observe(runtime, missing, None)
    runtime.clock.advance(2700)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "pre_sleep"
    assert machine(runtime)["deadline"] == before["deadline"]
    assert fields(runtime)["value"]["status"] == "unknown"
    assert (await command(runtime, "confirm_sleep"))["status"] == "accepted"
    runtime.clock.advance(1)
    await observe(runtime, "window_1", "open")
    assert machine(runtime)["state"] == "awake"


async def test_rapid_events_reset_each_time_but_sleep_volume_never_resets(runtime, config):
    await ready(runtime, config)
    await command(runtime, "start_pre_sleep")
    runtime.clock.advance(0.1)
    await observe(runtime, "terrace", "open")
    first = copy.deepcopy(machine(runtime))
    runtime.clock.advance(0.1)
    await observe(runtime, "terrace", "closed")
    assert machine(runtime)["deadline"]["deadline_id"] != first["deadline"]["deadline_id"]
    await command(runtime, "confirm_sleep")
    deadline = copy.deepcopy(machine(runtime)["deadline"])
    runtime.clock.advance(1)
    await observe(runtime, "volume", 11)
    assert machine(runtime)["deadline"] == deadline


async def test_sleep_to_pre_sleep_starts_new_episode_and_clears_capture(runtime, config):
    await ready(runtime, config)
    await command(runtime, "confirm_sleep")
    before = copy.deepcopy(machine(runtime))
    await command(runtime, "start_pre_sleep")
    assert machine(runtime)["state"] == "pre_sleep"
    assert machine(runtime)["episode_id"] != before["episode_id"]
    assert not machine(runtime).get("captures")


async def test_A2_explicit_start_is_unconditional_and_A3_automatic_conditions(runtime, config):
    await ready(runtime, config)
    await observe(runtime, "pc", True)
    await observe(runtime, "location", "away")
    assert (await command(runtime, "start_pre_sleep"))["status"] == "accepted"
    assert machine(runtime)["state"] == "pre_sleep"
    await observe(runtime, "pc", False)
    assert machine(runtime)["state"] == "awake"
    await observe(runtime, "location", "home")
    await observe(runtime, "tv", True)
    assert machine(runtime)["state"] == "awake"
    await observe(runtime, "permission", True)
    assert machine(runtime)["state"] == "pre_sleep"
    assert machine(runtime)["entry_label"] == "suspected"


@pytest.mark.parametrize("state", ["pre_sleep", "sleep"])
async def test_P7_S3_W3_plan_starts_waking_at_start_then_times_out(runtime, config, state):
    await ready(runtime, config, wake_time="13:02")
    await command(runtime, "start_pre_sleep" if state == "pre_sleep" else "confirm_sleep")
    runtime.clock.advance(119)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == state
    runtime.clock.advance(1)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "waking"
    assert machine(runtime)["entry_label"] == "schedule"
    runtime.clock.advance(3600)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "awake"
    assert machine(runtime)["episode_ended_at"]


@pytest.mark.parametrize(
    "name,target", [("confirm_sleep", "sleep"), ("start_pre_sleep", "pre_sleep")]
)
async def test_W4_W5_do_not_replay_consumed_wake_plan(runtime, config, name, target):
    await ready(runtime, config, wake_time="13:02")
    await command(runtime, "confirm_sleep")
    runtime.clock.advance(120)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "waking"
    await command(runtime, name)
    assert machine(runtime)["state"] == target
    runtime.clock.advance(1)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == target


@pytest.mark.parametrize("source,target", [("pc", True), ("location", "away")])
async def test_P5_P6_abort_pre_sleep_and_end_episode(runtime, config, source, target):
    await ready(runtime, config)
    await command(runtime, "start_pre_sleep")
    runtime.clock.advance(1)
    await observe(runtime, source, target)
    assert machine(runtime)["state"] == "awake"
    assert machine(runtime)["episode_ended_at"]
    assert machine(runtime)["deadline"] is None


@pytest.mark.parametrize("state", ["sleep", "waking"])
@pytest.mark.parametrize(
    "source,target",
    [
        ("pc", True),
        ("coffee", True),
        ("terrace", "open"),
        ("window_1", "open"),
        ("location", "away"),
    ],
)
async def test_S1_S2_W1_W2_all_wake_paths(runtime, config, state, source, target):
    await ready(runtime, config, wake_time="13:02")
    await command(runtime, "confirm_sleep")
    if state == "waking":
        runtime.clock.advance(120)
        await runtime.submit("tick")
    runtime.clock.advance(1)
    await observe(runtime, source, target)
    assert machine(runtime)["state"] == "awake"
    assert machine(runtime)["episode_ended_at"]


@pytest.mark.parametrize(
    "source,target",
    [
        ("hallway", True),
        ("terrace", "open"),
        ("toilet", True),
        ("coffee", True),
        ("bathlight", True),
        ("kitchen", True),
        ("volume", 11),
    ],
)
async def test_P4_each_allowlist_source_resets(runtime, config, source, target):
    await ready(runtime, config)
    if source in {"toilet", "bathlight", "kitchen"}:
        await observe(runtime, source, False, kind="snapshot")
    await command(runtime, "start_pre_sleep")
    runtime.clock.advance(60)
    await observe(runtime, source, target)
    assert datetime.fromisoformat(
        machine(runtime)["deadline"]["at"]
    ) == runtime.clock.now_utc() + timedelta(minutes=45)


@pytest.mark.parametrize(
    "source,target",
    [("hallway", True), ("toilet", True), ("bathlight", True), ("tv", True), ("volume", 11)],
)
async def test_S6_non_wake_changes_do_nothing(runtime, config, source, target):
    await ready(runtime, config)
    await command(runtime, "confirm_sleep")
    before = copy.deepcopy(machine(runtime))
    runtime.clock.advance(1)
    await observe(runtime, source, target)
    assert machine(runtime)["state"] == "sleep"
    assert machine(runtime)["deadline"] == before["deadline"]
    assert machine(runtime)["since_at"] == before["since_at"]


@pytest.mark.parametrize(
    "kind,retained,restored",
    [("snapshot", False, False), ("live_change", False, True), ("live_change", True, False)],
)
async def test_snapshot_restore_retained_never_wake(runtime, config, kind, retained, restored):
    await ready(runtime, config)
    await command(runtime, "confirm_sleep")
    runtime.clock.advance(1)
    await observe(runtime, "window_1", "open", kind=kind, retained=retained, restored=restored)
    assert machine(runtime)["state"] == "sleep"
    # Receipt/tick cannot replay a change that was not accepted as a fresh event.
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "sleep"
    runtime.clock.advance(1)
    await observe(runtime, "window_1", "closed")
    assert machine(runtime)["state"] == "awake"


async def test_same_request_id_is_one_reset_but_new_press_resets_again(runtime, config):
    await ready(runtime, config)
    identity = str(uuid4())
    first = await command(runtime, "confirm_sleep", identity)
    before = copy.deepcopy(machine(runtime))
    # Repeat the exact request, including timestamps, through the durable command log.
    row = await runtime.store.get("command_log", identity)
    runtime.clock.advance(10)
    assert await runtime.submit("command", row["command"]) == first
    assert machine(runtime) == before
    assert (await command(runtime, "confirm_sleep"))["status"] == "accepted"
    assert machine(runtime)["deadline"]["at"] != before["deadline"]["at"]
    assert machine(runtime)["captures"] == before["captures"]


async def test_unknown_tv_does_not_block_waking_timeout(runtime, config):
    await ready(runtime, config, wake_time="13:02")
    await command(runtime, "confirm_sleep")
    runtime.clock.advance(120)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "waking"
    await observe(runtime, "tv", None)
    await observe(runtime, "pc", None)
    runtime.clock.advance(3600)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "awake"


async def test_confirmed_sleep_deadline_is_consumed_without_transition(runtime, config):
    await ready(runtime, config)
    await command(runtime, "confirm_sleep")
    before = copy.deepcopy(machine(runtime))
    runtime.clock.advance(2700)
    await runtime.submit("tick")
    assert machine(runtime)["state"] == "sleep"
    assert machine(runtime)["deadline"]["fired"] is True
    assert machine(runtime)["since_at"] == before["since_at"]


async def test_committed_capture_and_deadline_survive_process_restart(runtime, config):
    from core_contracts.runtime import Runtime

    await ready(runtime, config)
    await command(runtime, "confirm_sleep")
    before = copy.deepcopy(machine(runtime))
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        for key in ("captures", "episode_id", "deadline", "since_at", "entry_label"):
            assert machine(restarted)[key] == before[key]
        assert machine(restarted)["state"] == "sleep"
    finally:
        await restarted.stop()
