"""B1-01..B1-19: synthetic device evidence; no household addresses or values."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from pydantic import ValidationError

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.evidence import Observation
from core_contracts.openings import OpeningParameters, dual_contact
from core_contracts.persistence import MemoryStore
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.registry import fingerprints, validate
from core_contracts.runtime import Runtime
from dev.fake_ha import FakeHA
from tests.conftest import activate_draft

from .conftest import activate

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def configuration():
    data = dict(
        config_schema_version=2,
        fingerprint_version=2,
        dependencies=[dict(dependency_id="test.mqtt", kind="mqtt", monitor="internal")],
        sources=[],
        bindings=[],
        contracts=[],
        catalogs=[
            dict(
                catalog_id="test.inversion",
                kind="map",
                version=1,
                entries={"True": False, "False": True},
            )
        ],
    )
    for index in range(4):
        dual = index < 3
        inputs = []
        for name in ("open", "tilt") if dual else ("contact",):
            key = f"source.test.{index}.{name}"
            data["sources"].append(
                dict(
                    source_id=key,
                    source_type="boolean",
                    source_origin="local",
                    freshness=dict(mode="report_heartbeat", interval_s=8400, future_tolerance_s=0),
                    dependency_id="test.mqtt",
                    physical_source_key=key,
                    value_schema=dict(type="bool", required=True, physical_state=True),
                )
            )
            data["bindings"].append(
                dict(
                    binding_id=key,
                    source_id=key,
                    adapter=dict(
                        kind="mqtt",
                        mqtt_topic=f"test/beta/{index}/{name}",
                        mqtt_value_path="contact",
                        mqtt_time_path="last_seen",
                    ),
                    transform=dict(kind="map_ref", catalog_id="test.inversion"),
                )
            )
            inputs.append(dict(name=name, ref=key))
        data["contracts"].append(
            dict(
                contract_id=f"test.opening{index}",
                type_id="opening.dual_contact" if dual else "opening.single_contact",
                type_version=1,
                enabled=True,
                inputs=inputs,
                parameters=dict(grace_s=600),
            )
        )
    for name, count in (("windows", 2), ("area", 3), ("all", 4)):
        data["contracts"].append(
            dict(
                contract_id=f"test.zone.{name}",
                type_id="opening_zone.facts",
                type_version=1,
                enabled=True,
                inputs=[
                    dict(name=f"member_{i + 1}", ref=f"test.opening{i}.value") for i in range(count)
                ],
                parameters={},
            )
        )
    return data


async def make_runtime(store=None, clock=None):
    runtime = Runtime(
        store or MemoryStore(), clock or FakeClock(NOW), type_registry(include_test_types=True)
    )
    await runtime.start()
    if runtime.config is None:
        await activate(runtime, configuration())
    return runtime


def field(runtime, key="test.opening0", name="value"):
    return runtime.snapshot()["contracts"][key]["fields"][name]


async def report(
    runtime, index=0, name="open", value=False, *, retained=False, stamp=None, raw=None
):
    binding = next(
        b for b in runtime.config.bindings if b.source_id == f"source.test.{index}.{name}"
    )
    adapter = MQTTAdapter(runtime, None, "disabled", {}, "")
    adapter.sequence = max(
        (o["ingest_seq"] for o in runtime.state.tables["source_observation_current"].values()),
        default=0,
    )
    payload = json.dumps(
        dict(contact=not value, last_seen=(stamp or runtime.clock.now_utc()).isoformat())
        if raw is None
        else raw
    ).encode()
    observation = adapter.observation(binding, runtime.config, payload, retained, 0)
    await runtime.submit("observation", observation)
    return observation


async def closed(runtime, index=0):
    await report(runtime, index, "open" if index < 3 else "contact", False)
    if index < 3:
        await report(runtime, index, "tilt", False)


def contact(value):
    return FieldValue(
        status="valid",
        value=value,
        quality="healthy",
        freshness="fresh",
        evidence=("test",),
        measured_at=NOW,
    )


@pytest.mark.parametrize(
    "opened,tilted,state",
    [(False, False, "closed"), (False, True, "tilted"), (True, True, "open"), (True, False, None)],
)
def test_B1_02_03_truth_table(opened, tilted, state):
    result = dual_contact([contact(opened), contact(tilted)], NOW)
    assert result.value == state
    assert result.status == ("valid" if state else "unknown")
    if state:
        assert result.freshness == "fresh"
    else:
        assert result.quality == "degraded" and result.reasons[0].code == ReasonCode.CONFLICT


@pytest.mark.parametrize(
    "opened,tilted,expected",
    [
        (True, None, "open"),
        (False, None, None),
        (None, True, None),
        (None, False, None),
        (None, None, None),
    ],
)
def test_B1_04_asymmetry(opened, tilted, expected):
    values = [
        unknown(ReasonCode.INPUT_STALE, "test", NOW) if v is None else contact(v)
        for v in (opened, tilted)
    ]
    result = dual_contact(values, NOW)
    assert result.value == expected
    assert result.quality == "degraded"
    if expected:
        assert result.reasons[0].code == ReasonCode.PARTIAL_EVIDENCE


@pytest.mark.parametrize("grace", [None, 0, -1, True, "600", float("nan"), float("inf")])
def test_B1_14_strict_required_grace(grace):
    with pytest.raises(ValidationError):
        OpeningParameters.model_validate({} if grace is None else dict(grace_s=grace))


async def test_B1_01_02_initial_and_single_contact():
    rt = await make_runtime()
    try:
        assert field(rt)["status"] == "unknown"
        await closed(rt, 3)
        assert field(rt, "test.opening3")["value"] == "closed"
        rt.clock.advance(1)
        observation = await report(rt, 3, "contact", True)
        assert observation.value_raw is False and observation.value is True
        assert field(rt, "test.opening3")["value"] == "open"
        rt.clock.advance(1)
        await report(
            rt, 3, "contact", raw=dict(contact="invalid", last_seen=rt.clock.now_utc().isoformat())
        )
        assert field(rt, "test.opening3")["status"] in {"held", "unknown"}
        assert not rt.state.tables["source_observation_current"]["source.test.3.contact"].get(
            "mqtt_device_proof"
        )
    finally:
        await rt.stop()


async def test_B1_05_06_11_12_heartbeat_and_grace_deadlines():
    rt = await make_runtime()
    try:
        for i in range(4):
            await closed(rt, i)
        since = field(rt, "test.zone.area", "all_closed")["since_at"]
        rt.clock.advance(1)
        await closed(rt)
        assert field(rt, "test.zone.area", "all_closed")["since_at"] == since
        rt.clock.advance(8400)
        await rt.submit("tick")
        assert field(rt)["status"] == "valid"
        rt.clock.advance(0.001)
        await rt.submit("tick")
        held = field(rt)
        assert held["status"] == "held"
        assert field(rt, "test.zone.area", "all_closed")["value"] is False
        assert field(rt, "test.zone.area", "any_held")["value"] is True
        deadline = held["held_until"]
        await rt.submit("bridge_state", ("unavailable", "disconnected"))
        assert field(rt)["held_until"] == deadline
        rt.clock.advance(100)
        await rt.submit("tick")
        assert field(rt)["held_until"] == deadline
        rt.clock.advance(500)
        await rt.submit("tick")
        assert field(rt)["status"] == "unknown"
        assert field(rt, "test.zone.area", "unclear")["value"] is True
        await closed(rt)
        assert field(rt)["value"] == "closed"
    finally:
        await rt.stop()


async def test_B1_06_late_scheduler_does_not_extend_grace():
    rt = await make_runtime()
    try:
        await closed(rt)
        # Advance the clock without dispatching its scheduled callbacks.
        rt.clock.advance(9100)
        await rt.submit("tick")
        assert field(rt)["status"] == "unknown"
    finally:
        await rt.stop()


async def test_B1_07_08_09_restore_proof_no_new_grace_or_continuity():
    rt = await make_runtime()
    await closed(rt)
    old_ref = field(rt)["evidence"]
    old_since = field(rt, "test.zone.windows", "all_closed")["since_at"]
    await rt.stop()
    rt.clock.advance(60)
    rt = await make_runtime(rt.store, rt.clock)
    try:
        assert field(rt)["value"] == "closed"
        assert field(rt)["evidence"] == old_ref
        assert field(rt, "test.zone.windows", "all_closed")["since_at"] != old_since
        assert not rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_armed"]
        rt.clock.advance(8400)
        await rt.submit("tick")
        assert field(rt)["status"] == "unknown"
        await closed(rt)
        rt.clock.advance(8401)
        await rt.submit("tick")
        deadline = field(rt)["held_until"]
        await rt.stop()
        rt.clock.advance(10)
        rt = await make_runtime(rt.store, rt.clock)
        assert field(rt)["status"] == "held" and field(rt)["held_until"] == deadline
        rt.clock.advance(600)
        await rt.submit("tick")
        assert field(rt)["status"] == "unknown"
    finally:
        await rt.stop()


@pytest.mark.parametrize("change", ["legacy", "retained", "future", "negative", "fingerprint"])
async def test_B1_07_restore_rejects_unproven_reports(change):
    rt = await make_runtime()
    await closed(rt)
    await rt.stop()
    saved = rt.store.state.tables["source_observation_current"]["source.test.0.open"]
    if change == "legacy":
        saved.pop("mqtt_device_proof", None)
    elif change == "retained":
        saved["mqtt_retained"] = True
    elif change == "future":
        saved["device_time"] = (NOW + timedelta(days=1)).isoformat()
    elif change == "negative":
        saved["availability"] = "unavailable"
    else:
        saved["mqtt_device_proof"] = "different"
    rt = await make_runtime(rt.store, rt.clock)
    try:
        assert field(rt)["status"] == "unknown"
    finally:
        await rt.stop()


async def test_B1_10_retained_replay_cannot_revive_dead_device():
    rt = await make_runtime()
    try:
        await closed(rt)
        rt.clock.advance(10000)
        await rt.submit("tick")
        await report(rt, stamp=NOW)
        await report(rt, stamp=NOW, retained=True)
        assert field(rt)["status"] == "unknown"
        await closed(rt)
        assert field(rt)["value"] == "closed"
    finally:
        await rt.stop()


async def test_B1_03_04_conflict_cancels_hold_open_immediate():
    rt = await make_runtime()
    try:
        await closed(rt)
        rt.clock.advance(1)
        await report(rt, value=True)
        assert field(rt)["status"] == "unknown"
        assert field(rt)["reasons"][0]["code"] == "conflict"
        await report(rt, name="tilt", value=True)
        assert field(rt)["value"] == "open"
        rt.clock.advance(8401)
        await rt.submit("tick")
        assert field(rt)["status"] == "held"
        assert field(rt, "test.zone.windows", "any_open")["value"] is True
        assert field(rt, "test.zone.windows", "any_held")["value"] is True
        # Resolve all other configured members; held alone is not unclear.
        await closed(rt, 1)
        assert field(rt, "test.zone.windows", "unclear")["value"] is False
        await report(rt, value=True)
        assert field(rt)["value"] == "open" and field(rt)["status"] == "valid"
        assert field(rt)["quality"] == "degraded"
    finally:
        await rt.stop()


async def test_B1_13_zones_and_B1_15_topic_rebinding():
    rt = await make_runtime()
    try:
        for i in range(4):
            await closed(rt, i)
        rt.clock.advance(1)
        await report(rt, 3, "contact", True)
        assert field(rt, "test.zone.all", "any_open")["value"] is True
        assert field(rt, "test.zone.area", "all_closed")["value"] is True
        assert field(rt, "test.zone.windows", "all_closed")["value"] is True
        await report(rt, 2, "open", True)
        await report(rt, 2, "tilt", True)
        assert field(rt, "test.zone.area", "any_open")["value"] is True
        assert field(rt, "test.zone.windows", "all_closed")["value"] is True
        doc = configuration()
        doc["bindings"][0]["adapter"]["mqtt_topic"] = "test/beta/replacement"
        before = fingerprints(rt.config, rt.types)
        old_observation = Observation.model_validate(
            rt.state.tables["source_observation_current"]["source.test.0.open"]
        )
        after = fingerprints(validate(doc, rt.types), rt.types)
        assert before["test.zone.windows"] != after["test.zone.windows"]
        await activate(rt, doc)
        assert field(rt)["status"] == "unknown"
        assert "source.test.0.open" not in rt.state.tables["source_observation_current"]
        assert not rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_until"]
        rt.ingest(old_observation.model_copy(update={"registry_revision": 1}))
        await rt.queue.join()
        assert field(rt)["status"] == "unknown"
    finally:
        await rt.stop()


async def test_B1_18_commit_failure_does_not_publish_context():
    rt = await make_runtime()
    try:
        await closed(rt)
        before = copy.deepcopy(rt.snapshot())
        nodes = copy.deepcopy(rt.state.tables["node_state"])
        rt.clock.advance(1)
        rt.store.before_commit = lambda: (_ for _ in ()).throw(OSError("synthetic failure"))
        with pytest.raises(OSError):
            await report(rt, value=True)
        assert rt.snapshot()["contracts"] == before["contracts"]
        assert rt.snapshot()["publication_seq"] == before["publication_seq"]
        assert rt.state.tables["node_state"] == nodes
        rt.store.before_commit = None
    finally:
        await rt.stop()


def test_B1_19_production_types_schema_and_invalid_inputs():
    registry = type_registry(include_test_types=True)
    for name in ("opening.dual_contact", "opening.single_contact", "opening_zone.facts"):
        assert not registry.get(name, 1).schema.fixture
    data = configuration()
    data["contracts"][-1]["inputs"][1]["ref"] = "test.opening0.value"
    with pytest.raises(ValueError, match="distinct opening members"):
        validate(data, type_registry(include_test_types=True))


def connectivity_definition():
    schema = dict(
        type="enum",
        required=True,
        physical_state=False,
        value_catalog=["ready", "starting", "unavailable"],
    )
    return dict(
        type_id="user.connectivity",
        type_version=1,
        inputs=[dict(name="connection", schema=schema)],
        schema=dict(fields={"value": schema}, available_projection=["value"], fixture=False),
        outputs={"value": dict(op="input", name="connection")},
    )


def with_connectivity(data):
    data = copy.deepcopy(data)
    data["dependencies"].append(dict(dependency_id="test.ha", kind="ha", monitor="internal"))
    data["sources"].append(
        dict(
            source_id="source.test.connection",
            source_type="state",
            source_origin="local",
            freshness=dict(mode="event_stateful", future_tolerance_s=0),
            dependency_id="test.ha",
            physical_source_key="test.connection",
            value_schema=connectivity_definition()["inputs"][0]["schema"],
        )
    )
    data["bindings"].append(
        dict(
            binding_id="test.connection",
            source_id="source.test.connection",
            adapter=dict(kind="connectivity"),
        )
    )
    data["definitions"] = [connectivity_definition()]
    data["contracts"].append(
        dict(
            contract_id="test.connectivity",
            type_id="user.connectivity",
            type_version=1,
            inputs=[dict(name="connection", ref="source.test.connection")],
            parameters={},
            enabled=True,
        )
    )
    return data


async def test_B1_16_connectivity_only_connects_mock_traber_without_changing_openings(
    aiohttp_server,
):
    rt = await make_runtime()
    try:
        await activate(rt, with_connectivity(configuration()))
        await closed(rt)
        assert rt.requires_ha_snapshot
        before = field(rt)["value"]
        server = await aiohttp_server(FakeHA(v2=True).application())
        async with aiohttp.ClientSession() as session:
            adapter = BridgeAdapter(
                rt, session, str(server.make_url("/websocket")).replace("http:", "ws:"), "synthetic"
            )
            task = asyncio.create_task(adapter.connect(rt.config))
            async with asyncio.timeout(5):
                while not rt.first_snapshot:
                    await asyncio.sleep(0)  # noqa: TID251
            assert rt.bridge_version == "test"
            assert field(rt, "test.connectivity")["value"] == "ready"
            rt.resubscribe.set()
            await task
        await rt.submit("bridge_state", ("connected", "starting"))
        assert field(rt, "test.connectivity")["value"] == "starting"
        assert field(rt)["value"] == before
        assert rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_armed"]
        await rt.submit("bridge_state", ("unavailable", "disconnected"))
        assert field(rt, "test.connectivity")["value"] == "unavailable"
        assert field(rt)["value"] == before
    finally:
        await rt.stop()


async def test_B1_14_parameter_source_edits_create_baseline_without_type_version():
    rt = await make_runtime()
    try:
        await closed(rt)
        data = configuration()
        data["contracts"][0]["parameters"]["grace_s"] = 300
        await activate(rt, data)
        assert rt.config.contracts[0].type_version == 1
        assert not rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_armed"]
        data["sources"][0]["freshness"]["interval_s"] = 9000
        await activate(rt, data)
        # b4 F3: an interval edit reassesses the original report without losing it.
        assert field(rt)["status"] == "valid"
        assert not rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_until"]
    finally:
        await rt.stop()


async def test_B1_17_category_export_import_roundtrip_and_legacy():
    from .test_portable import export, plan, stage

    rt = await make_runtime()
    try:
        await rt.submit(
            "editorial_put",
            dict(
                request=dict(
                    object=dict(kind="contract", id="test.opening0"),
                    expected_editorial_version=0,
                    category="openings",
                ),
                _actor=dict(kind="internal", role="system"),
            ),
        )
        package = await export(rt)
        assert package["editorial"][0]["category"] == "openings"
        preview = await plan(rt, package)
        assert preview["unresolved"] == []
        draft = await stage(rt, package, preview)
        assert rt.state.active_revision == 1
        await activate_draft(rt, draft, expected_active_revision=1)
        assert (
            rt.state.tables["editorial_current"]["contract:test.opening0"]["category"] == "openings"
        )
        old = await plan(rt, configuration())
        assert old["package"]["legacy"]
        assert (
            rt.state.tables["editorial_current"]["contract:test.opening0"]["category"] == "openings"
        )
    finally:
        await rt.stop()
    from core_contracts.testing.contract_types import type_registry as fixture_types

    with pytest.raises(ValueError, match="type or parameters"):
        validate(configuration(), fixture_types())
    data = configuration()
    data["sources"][0]["value_schema"]["type"] = "number"
    with pytest.raises(ValueError):
        validate(data, type_registry(include_test_types=True))


@pytest.mark.parametrize(
    "left",
    [
        "closed",
        "tilted",
        "open",
        "held_closed",
        "held_tilted",
        "held_open",
        "unknown",
        "unavailable",
        "conflict",
    ],
)
@pytest.mark.parametrize(
    "right",
    [
        "closed",
        "tilted",
        "open",
        "held_closed",
        "held_tilted",
        "held_open",
        "unknown",
        "unavailable",
        "conflict",
    ],
)
def test_B1_11_12_zone_status_cross_product(left, right):
    from core_contracts.openings import zone
    from core_contracts.producer import Context
    from core_contracts.registry import Contract

    clock = FakeClock(NOW)

    def member(name):
        if name in {"closed", "tilted", "open"}:
            return FieldValue(status="valid", value=name, quality="healthy", freshness="fresh")
        if name.startswith("held_"):
            return FieldValue(
                status="held",
                value=name.removeprefix("held_"),
                quality="healthy",
                grace_declared=True,
                held_until=NOW + timedelta(seconds=10),
                reasons=[dict(code=ReasonCode.GRACE, input="test", since=NOW)],
            )
        return unknown(
            ReasonCode.CONFLICT
            if name == "conflict"
            else ReasonCode.INPUT_UNAVAILABLE
            if name == "unavailable"
            else ReasonCode.INPUT_UNKNOWN,
            "test",
            NOW,
        )

    values = [member(left), member(right)]
    contract = Contract.model_validate(configuration()["contracts"][4])
    rt = Runtime(MemoryStore(), clock, type_registry(include_test_types=True))
    ctx = Context(
        clock,
        {},
        contract,
        dict(zip(("member_1", "member_2"), values)),
        {"fingerprint": "test"},
        rt.state,
        "input",
        "first",
        None,
    )
    facts = zone(ctx)
    assert facts["any_open"].value == any(v.usable(NOW) and v.value == "open" for v in values)
    assert facts["any_tilted"].value == any(v.usable(NOW) and v.value == "tilted" for v in values)
    assert facts["any_held"].value == any(v.status == "held" for v in values)
    assert facts["unclear"].value == any(v.status == "unknown" for v in values)
    assert facts["all_closed"].value == (left == right == "closed")
    assert all(f.status == "valid" and f.since_at == NOW for f in facts.values())
    clock.advance(1)
    assert all(f.since_at == NOW for f in zone(ctx).values())


async def test_B1_09_repeated_restarts_preserve_original_report_identity():
    rt = await make_runtime()
    await closed(rt)
    original = field(rt)["evidence"]
    for _ in range(2):
        await rt.stop()
        rt.clock.advance(1)
        rt = await make_runtime(rt.store, rt.clock)
        assert field(rt)["value"] == "closed"
        assert field(rt)["evidence"] == original
    await rt.stop()


@pytest.mark.parametrize("mode", ["event_stateful", "periodic_ttl"])
def test_B1_19_opening_requires_explicit_heartbeat_or_liveness(mode):
    data = configuration()
    data["sources"][0]["freshness"]["mode"] = mode
    with pytest.raises(ValueError, match="heartbeat or explicit liveness"):
        validate(data, type_registry(include_test_types=True))


@pytest.mark.parametrize("interval", [True, "8400", float("inf"), float("nan")])
def test_B1_14_heartbeat_interval_is_finite_and_strict(interval):
    data = configuration()
    data["sources"][0]["freshness"]["interval_s"] = interval
    with pytest.raises(ValueError):
        validate(data, type_registry(include_test_types=True))
