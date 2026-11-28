"""Synthetic b33 truth trajectory and short shadow-inventory profile."""

import asyncio
import json
import logging
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from time import perf_counter
from uuid import UUID

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter, WebSocketRequests
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.clock import FakeClock, SystemClock
from core_contracts.contract_types import type_registry
from core_contracts.extensions.catalog.managed_catalog import head, head_key, prepare
from core_contracts.freshness_templates import upgrade_document
from core_contracts.model import canonical, checksum
from core_contracts.persistence import MemoryStore, PostgresStore
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange
from core_contracts.runtime import Runtime
from dev.truth_lane_profile import Timings, distribution

from .conftest import activate
from .test_catalog_playback import player_document, player_event
from .test_managed_catalog import change
from .test_postgres import MIGRATIONS, database

__all__ = ["database"]

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = FIXTURES / "replay" / "truth-shadow-b33.json"
BASELINE = "8146b64d3f2e126491d0baacf2c73de779ad6325"
START = datetime(2026, 1, 1, 12, tzinfo=UTC)
INSTALLATION = str(UUID(int=189))


def shadow_document(config, scale=1):
    presence = json.loads((FIXTURES / "presence-package-v1.json").read_text(encoding="utf-8"))[
        "config"
    ]
    presence = upgrade_document(presence)
    presence["contracts"] = [
        row for row in presence["contracts"] if row["type_id"] != "synthetic.connectivity"
    ]
    document = player_document(config)
    applicable = next(row for row in document["sources"] if row["source_id"] == "source.applicable")
    applicable.pop("origin")
    applicable["dependency_id"] = "dep.ha"
    binding = next(row for row in document["bindings"] if row["source_id"] == "source.applicable")
    binding["adapter"] = {"kind": "ha_state", "ha_entity_id": "binary_sensor.shadow_media"}
    binding["transform"] = {"kind": "type_cast", "target": "bool"}
    for section in ("dependencies", "definitions", "catalogs"):
        document[section] = document.get(section, []) + presence.get(section, [])
    inventory = {"sources": "source_id", "bindings": "binding_id", "contracts": "contract_id"}
    for index in range(6 * scale):
        ids = {
            item[key]: f"{item[key]}.shadow{index}"
            for section, key in inventory.items()
            for item in presence[section]
        }

        def replace(value):
            if isinstance(value, list):
                return [replace(child) for child in value]
            if isinstance(value, dict):
                return {key: replace(child) for key, child in value.items()}
            if not isinstance(value, str) or value in ids:
                return ids[value] if isinstance(value, str) else value
            owner, _, field = value.rpartition(".")
            return f"{ids[owner]}.{field}" if owner in ids else value

        for section in inventory:
            for item in presence[section]:
                clone = replace(item)
                if clone.get("physical_source_key"):
                    clone["physical_source_key"] += f".shadow{index}"
                if adapter := clone.get("adapter"):
                    if adapter.get("ha_entity_id"):
                        adapter["ha_entity_id"] += f"_shadow{index}"
                    if adapter.get("mqtt_topic"):
                        adapter["mqtt_topic"] += f"/shadow{index}"
                document[section].append(clone)
    control = upgrade_document(config)
    for section in ("dependencies", *inventory):
        document[section].extend(control[section])
    for item in document["contracts"]:
        item["ha_projection"] = item["type_id"] != "test.echo"
    media = deepcopy(document["contracts"][0])
    media["contract_id"] = "fixture.media_secondary"
    document["contracts"].append(media)
    document["notifications"] = {
        "signal": {"enabled": True, "debounce_s": 5, "clear_after_s": 5, "min_update_s": 30},
        "configuration": {"enabled": True, "debounce_s": 5, "clear_after_s": 5, "min_update_s": 30},
        "max_items": 10,
    }
    return document


async def seed_catalog(runtime, scale):
    state = runtime.state.catalog_clone()
    catalog = next(c for c in runtime.config.catalogs if c.catalog_id == "media.homepods")
    write = CatalogWrite.model_construct(
        catalog_id=catalog.catalog_id,
        definition_version=catalog.version,
        expected_active_revision=state.active_revision,
        expected_content_revision=0,
        request_id=str(UUID(int=190)),
        reason="Synthetic shadow inventory",
        changes=tuple(
            EntryChange.model_validate(
                change(
                    canonical(["Synthetic Artist", f"Synthetic Title {index:05}"]),
                    scope="homepods",
                    classification="boost",
                )
            )
            for index in range(3500 * scale)
        ),
    )
    prepare(state, runtime.config, write, None, START, expanded_limit=100_000)
    key = head_key(catalog)
    for identity, entry in head(state, catalog)["entries"].items():
        state.tables["catalog_sighting"][f"{key}:{identity}"] = {
            **{field: entry[field] for field in ("scope", "media_type", "signal_type", "key")},
            "catalog": key,
            "identity": identity,
            "display_name": f"Synthetic sighting {identity[:12]}",
            "first_seen": START.isoformat(),
            "last_seen": START.isoformat(),
            "seen_count": 1,
            "source": "synthetic-seed",
        }
    await runtime._commit(state)


class ProjectionPeer:
    """A synthetic transport peer exercising the real sender and its ACK handling."""

    def __init__(self):
        self.connection = WebSocketRequests(self)
        self.projections = []
        self.changed = asyncio.Event()

    async def send_json(self, request, *, dumps=json.dumps):
        payload = json.loads(dumps(request))
        future = self.connection.pending[payload.pop("id")]
        self.projections.append(payload)
        future.set_result({"applied": True})
        self.changed.set()


async def ha_state(runtime, bridge, entity, value):
    now = runtime.clock.now_utc().isoformat()
    await bridge.event(
        {
            "kind": "changed",
            "time_fired": now,
            "entity_id": entity,
            "new_state": {
                "state": value,
                "attributes": {},
                "last_changed": now,
                "last_updated": now,
            },
        },
        runtime.config,
    )
    await runtime.queue.join()


async def report(runtime, adapter, binding, measured, value, *, retained=False):
    payload = json.dumps(
        {binding.adapter.mqtt_value_path: value, "last_seen": measured.isoformat()}
    ).encode()
    observation = adapter.observation(binding, runtime.config, payload, retained, 1)
    assert observation is not None
    runtime.ingest(observation)
    await runtime.queue.join()


async def trajectory(store, config, monkeypatch, *, seconds=0):
    ids = count(1000)
    monkeypatch.setattr("core_contracts.runtime.uuid4", lambda: UUID(int=next(ids)))
    monkeypatch.setattr("core_contracts.notifications.uuid4", lambda: UUID(int=next(ids)))
    episodes = count(100_000)
    monkeypatch.setattr("core_contracts.statemachine.uuid4", lambda: UUID(int=next(episodes)))
    clock = FakeClock(START)
    runtime = Runtime(store, clock, type_registry(include_test_types=True))
    runtime.installation_id = INSTALLATION
    await runtime.start()
    try:
        await activate(runtime, shadow_document(config))
        await seed_catalog(runtime, 1)
        await runtime.submit("mqtt_state", "connected")
        await runtime.submit("bridge_state", ("connected", "connected"))
        runtime.bridge_protocol_selected = 2
        runtime.bridge_features = ["contract_projection", "contract_projection_fields"]
        runtime.first_snapshot = True
        async with aiohttp.ClientSession() as session:
            bridge = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
            peer = ProjectionPeer()
            bridge.connection = peer.connection
            bridge.projection_supported = bridge.projection_fields_supported = True
            bridge.projection_connected.set()
            mqtt = MQTTAdapter(runtime, session, "disabled", {}, "")
            projection = asyncio.create_task(bridge.projection_worker())
            try:
                return await exercise(runtime, bridge, peer, mqtt, seconds)
            finally:
                projection.cancel()
                await asyncio.gather(projection, return_exceptions=True)
    finally:
        await runtime.stop()


async def exercise(runtime, bridge, peer, mqtt, seconds):
    trace = []
    tick_counts = []
    last_projection = -1

    async def checkpoint(label):
        nonlocal last_projection
        await runtime.queue.join()
        sequence = runtime.state.publication_seq
        if last_projection != sequence:
            async with asyncio.timeout(5):
                while not peer.projections or peer.projections[-1]["publication_seq"] != sequence:
                    peer.changed.clear()
                    await peer.changed.wait()
            last_projection = sequence
        tables = runtime.state.tables
        trace.append(
            {
                "event": label,
                "observations": checksum(tables["source_observation_current"]),
                "fields": checksum(tables["contract_state_current"]),
                "side_effects": checksum(
                    {
                        "catalog_sighting": {
                            k: row
                            for k, row in tables["catalog_sighting"].items()
                            if row["source"] != "synthetic-seed" or row["seen_count"] > 1
                        },
                        "notices": {
                            k: v
                            for k, v in tables["service_state"].items()
                            if k.startswith("notice:")
                        },
                        **{
                            name: tables[name]
                            for name in ("node_state", "sm_instance", "service_job")
                        },
                    }
                ),
                "projections": checksum(peer.projections),
            }
        )

    await checkpoint("ready")
    await ha_state(runtime, bridge, "sensor.example_1", "on")
    await checkpoint("ha-on")
    await ha_state(runtime, bridge, "binary_sensor.shadow_media", "on")
    await checkpoint("media-applicable")
    sensors = [
        binding
        for binding in runtime.config.bindings
        if binding.adapter.mqtt_time_path == "last_seen"
        and binding.adapter.mqtt_value_path == "contact"
    ][:4]
    assert len(sensors) == 4
    previous = 0.0
    start = perf_counter()
    pacing_clock = SystemClock()
    for cycle in range(3):
        for index, (offset, binding) in enumerate(zip((4, 17, 34, 52), sensors, strict=True)):
            at = cycle * 60 + offset
            if seconds:
                await pacing_clock.sleep(max(0.0, start + at * seconds / 180 - perf_counter()))
            runtime.clock.advance(at - previous)
            previous = at
            now = runtime.clock.now_utc()
            await report(runtime, mqtt, binding, now, bool(index % 2))
            await checkpoint(f"report-{cycle}-{index}")
            if cycle == 0 and index == 0:
                accepted = deepcopy(
                    runtime.state.tables["source_observation_current"][binding.binding_id]
                )
                assert accepted["mqtt_device_proof"]
                await report(runtime, mqtt, binding, now, False, retained=True)
                await checkpoint("retained")
                await report(runtime, mqtt, binding, now - timedelta(seconds=1), True)
                await checkpoint("late")
                assert (
                    runtime.state.tables["source_observation_current"][binding.binding_id]
                    == accepted
                )
            if cycle == 0 and index == 1:
                await player_event(runtime, bridge, "Synthetic Artist", "Synthetic Title 00000")
                previous += 0.01
                assert (
                    runtime.snapshot()["contracts"]["fixture.media"]["fields"]["value"]["value"]
                    == "boost"
                )
                await checkpoint("catalog-hit")
            if cycle == 1 and index == 0:
                await player_event(runtime, bridge, "Synthetic Artist", "Synthetic Unassigned")
                previous += 0.01
                await checkpoint("catalog-miss")
        await runtime.submit("tick")
        tick_counts.append(runtime.evaluation_stats["evaluated"])
        await checkpoint(f"tick-{cycle}")
    assert any(job["kind"] == "notice" for job in runtime.state.tables["service_job"].values())
    while job := await runtime.submit("service_claim"):
        await runtime.submit(
            "service_complete", {"job_id": job["job_id"], "response": {"outcome": "completed"}}
        )
    await checkpoint("service-claims")
    return trace, tick_counts


def assert_reference(trace):
    actual = {"reference_commit": BASELINE, "trace": trace}
    if os.getenv("WUSCHEL_SHADOW_RECORD_B33") == BASELINE:
        REFERENCE.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert actual == json.loads(REFERENCE.read_text(encoding="utf-8"))


async def test_b33_shadow_trajectory(config, monkeypatch):
    trace, _ = await trajectory(MemoryStore(), config, monkeypatch)
    assert_reference(trace)


@pytest.mark.postgres
async def test_b33_shadow_postgres(database, config, monkeypatch):
    seconds = int(os.getenv("WUSCHEL_SHADOW_PROFILE_SECONDS", "0"))
    timings = Timings()
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    logger = logging.getLogger("core_contracts")
    logger.addHandler(timings)
    old_level = logger.level
    if seconds:
        logger.setLevel(logging.DEBUG)
    try:
        trace, tick_counts = await trajectory(store, config, monkeypatch, seconds=seconds)
        assert_reference(trace)
    finally:
        logger.setLevel(old_level)
        logger.removeHandler(timings)
        await store.close()
    if seconds:
        print(
            json.dumps(
                {
                    "tick_evaluated": tick_counts,
                    "timing": {
                        name: distribution(values) for name, values in timings.measurements.items()
                    },
                },
                sort_keys=True,
            )
        )
