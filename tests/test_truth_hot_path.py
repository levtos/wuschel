"""Deterministic work counters for the hot paths covered by #189 step 1."""

import ast
import inspect
from collections import Counter
from textwrap import dedent

import aiohttp
import pytest

from core_contracts import registry, slots
from core_contracts import runtime as lane
from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.extensions.catalog import catalog_hierarchy, catalog_playback, managed_catalog
from core_contracts.mqtt_restore import restore
from core_contracts.persistence import MemoryStore, State
from core_contracts.registry import RegistryConfig
from core_contracts.state_tracking import RowDict, Rows

from .conftest import activate
from .test_catalog_playback import player_event
from .test_truth_shadow import START, ha_state, report, seed_catalog, shadow_document


def watch_work(monkeypatch):
    counts = Counter()
    validate = RegistryConfig.model_validate
    fingerprints = registry.fingerprints
    rows_init = Rows.__init__
    rows_get = Rows.__getitem__
    entry_get = RowDict.__getitem__

    def counted_validate(*args, **kwargs):
        counts["registry_validations"] += 1
        return validate(*args, **kwargs)

    def counted_fingerprints(*args, **kwargs):
        counts["fingerprints"] += 1
        return fingerprints(*args, **kwargs)

    def counted_table(self, changes, name, data, **kwargs):
        if name == "catalog_sighting":
            counts["sighting_rows"] += len(data)
        rows_init(self, changes, name, data, **kwargs)

    def counted_row(self, key):
        if self.name == "catalog_sighting":
            counts["sighting_rows"] += 1
        return rows_get(self, key)

    def counted_entry(self, key):
        if self.owner.table == "catalog_head" and key not in {
            "catalog_id",
            "definition_version",
            "definition_digest",
            "revision",
            "entries",
        }:
            counts["title_rows"] += 1
        return entry_get(self, key)

    monkeypatch.setattr(RegistryConfig, "model_validate", staticmethod(counted_validate))
    monkeypatch.setattr(registry, "fingerprints", counted_fingerprints)
    monkeypatch.setattr(lane, "fingerprints", counted_fingerprints)
    monkeypatch.setattr(Rows, "__init__", counted_table)
    monkeypatch.setattr(Rows, "__getitem__", counted_row)
    monkeypatch.setattr(RowDict, "__getitem__", counted_entry)
    return counts


@pytest.mark.parametrize("scale", [1, 2, 4])
async def test_shadow_single_event_work_is_independent_of_unrelated_inventory(
    config, monkeypatch, scale
):
    clock = FakeClock(START)
    store = MemoryStore()
    runtime = lane.Runtime(store, clock, type_registry(include_test_types=True))
    await runtime.start()
    try:
        await activate(runtime, shadow_document(config, replicas=4 * scale))
        await seed_catalog(runtime, scale)
        await runtime.submit("mqtt_state", "connected")
        await runtime.submit("bridge_state", ("connected", "connected"))
        runtime.first_snapshot = True
        binding = next(
            b for b in runtime.config.bindings if b.adapter.mqtt_time_path == "last_seen"
        )
        async with aiohttp.ClientSession() as session:
            bridge = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
            mqtt = MQTTAdapter(runtime, session, "disabled", {}, "")
            counts = watch_work(monkeypatch)
            await report(runtime, mqtt, binding, START, False)
            assert counts["fingerprints"] == 0
            accepted = runtime.state.tables["source_observation_current"][binding.binding_id]
            assert accepted["mqtt_device_proof"]
            before = len(store.state.tables["source_observation_history"])
            counts.clear()
            await report(runtime, mqtt, binding, START, False, retained=True)
            assert len(store.state.tables["source_observation_history"]) == before
            assert counts["fingerprints"] == 0

            counts.clear()
            await ha_state(runtime, bridge, "sensor.example_1", "on")
            assert (
                runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True
            )
            assert counts["sighting_rows"] == counts["title_rows"] == 0

            await ha_state(runtime, bridge, "binary_sensor.shadow_media", "on")
            counts.clear()
            await player_event(runtime, bridge, "Synthetic Artist", "Synthetic Title 00000")
            assert (
                runtime.snapshot()["contracts"]["fixture.media"]["fields"]["value"]["value"]
                == "boost"
            )
            assert counts["registry_validations"] == 0
            # Tick evaluation is recorded as a baseline for step 3.
            await runtime.submit("tick")
            print(f"shadow scale={scale} tick_evaluated={runtime.truth_timings.tick_evaluated}")
    finally:
        await runtime.stop()


def test_deferred_sighting_table_keeps_live_forks_isolated():
    state = State()
    state.tables["catalog_sighting"]["synthetic"] = {"metadata": {"label": "from b33"}}
    state.compact()
    old_table = dict.__getitem__(state.tables, "catalog_sighting")
    left, right = state.live_clone(), state.live_clone()
    assert dict.__getitem__(left.tables, "catalog_sighting") is old_table
    assert dict.__getitem__(right.tables, "catalog_sighting") is old_table

    left.tables["catalog_sighting"]["synthetic"]["metadata"]["label"] = "new sighting"
    assert left.table_differs(state, "catalog_sighting")
    assert not right.table_differs(state, "catalog_sighting")
    assert state.tables["catalog_sighting"]["synthetic"]["metadata"]["label"] == "from b33"
    assert right.tables["catalog_sighting"]["synthetic"]["metadata"]["label"] == "from b33"


def test_truth_hooks_and_resolvers_use_prepared_revision_data():
    hooks = {
        hook.handler
        for register in (
            slots.OBSERVATION,
            slots.AFTER_EVALUATE,
            slots.BEFORE_COMMIT,
            slots.NEXT_DUE,
        )
        for hook in register.values()
    }
    resolvers = {rule.evaluate for rule in slots.RULES.values() if rule.evaluate is not None}
    helpers = {
        restore,
        managed_catalog.lookup,
        catalog_hierarchy.lookup,
        catalog_hierarchy.source_frame,
        catalog_playback.offer,
    }
    failures = []
    for function in hooks | resolvers | helpers:
        tree = ast.parse(dedent(inspect.getsource(function)))
        aliases = {
            alias.asname: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            name = aliases.get(name, name)
            if name in {"fingerprints", "registry_types"} or (
                name == "model_validate"
                and isinstance(target, ast.Attribute)
                and ast.unparse(target.value).split(".")[-1] == "RegistryConfig"
            ):
                failures.append(f"{function.__module__}.{function.__name__}: {ast.unparse(target)}")
    assert not failures, sorted(failures)
