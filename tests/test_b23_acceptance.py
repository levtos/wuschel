"""Mandatory live-size acceptance, entirely synthetic and isolated from HA."""

import asyncio
import copy
import json
import threading
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import aiohttp

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.catalog_models import CatalogWrite
from core_contracts.clock import SystemClock
from core_contracts.contract_types import type_registry
from core_contracts.managed_catalog import prepare as prepare_catalog
from core_contracts.management import Planner, prepare
from core_contracts.operation_timing import percentile
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from dev.fake_ha import FakeHA

from .conftest import activate


async def scale_runtime(store=None, event_sources=5):
    runtime = Runtime(store or MemoryStore(), SystemClock(), type_registry())
    runtime.installation_id = str(uuid4())
    await runtime.start()
    config = {
        "config_schema_version": 3,
        "fingerprint_version": 3,
        "dependencies": [
            {"dependency_id": "dependency.synthetic", "kind": "ha", "monitor": "internal"}
        ],
        "sources": [
            {
                "source_id": f"source.synthetic{index}",
                "source_type": "event" if index < event_sources else "boolean",
                "value_schema": {"type": "bool", "required": True, "physical_state": False},
                "source_origin": "local",
                "dependency_id": "dependency.synthetic",
                "physical_source_key": f"physical.synthetic{index}",
                "freshness": {"mode": "event_stateful", "future_tolerance_s": 1},
            }
            for index in range(90)
        ],
        "bindings": [
            {
                "binding_id": f"binding.synthetic{index}",
                "source_id": f"source.synthetic{index}",
                "adapter": {"kind": "ha_state", "ha_entity_id": f"binary_sensor.synthetic_{index}"},
                "transform": {"kind": "type_cast", "target": "bool"},
            }
            for index in range(90)
        ],
        "contracts": [
            {
                "contract_id": f"synthetic.fact{index}",
                "type_id": "fixture.echo",
                "type_version": 1,
                "enabled": True,
                "ha_projection": True,
                "inputs": [{"name": "a", "ref": f"source.synthetic{index % 90}"}],
                "parameters": {},
            }
            for index in range(100)
        ],
        "definitions": [
            {
                "type_id": "fixture.echo",
                "type_version": 1,
                "inputs": [
                    {
                        "name": "a",
                        "schema": {"type": "bool", "required": True, "physical_state": False},
                    }
                ],
                "schema": {
                    "fields": {
                        "value": {"type": "bool", "required": True, "physical_state": False}
                    },
                    "available_projection": ["value"],
                },
                "outputs": {"value": {"op": "input", "name": "a"}},
            }
        ],
        "catalogs": [
            {
                "catalog_id": f"synthetic.catalog{index}",
                "kind": "managed",
                "version": 1,
                "identity_rule": {
                    "version": 1,
                    "fields": ["scope", "media_type", "signal_type", "normalized_key"],
                    "normalizer": "normalize_key",
                },
                "classification_schema": {"type": "enum", "values": ["other", "example"]},
                "unassigned_value": "other",
                "master_depth": 1,
            }
            for index in range(7)
        ],
    }
    try:
        for _ in range(11):
            await activate(runtime, config)
        # Build the isolated fixture with validated, bounded requests in one
        # transaction, rather than benchmarking setup's 112 individual commits.
        staged = runtime.state.clone()
        for selected in config["catalogs"]:
            for batch in range(16):
                prepare_catalog(
                    staged,
                    runtime.config,
                    CatalogWrite.model_validate(
                        {
                            "catalog_id": selected["catalog_id"],
                            "definition_version": 1,
                            "expected_active_revision": 11,
                            "expected_content_revision": batch,
                            "request_id": str(uuid4()),
                            "reason": "Synthetic acceptance stock",
                            "changes": [
                                {
                                    "scope": "synthetic",
                                    "media_type": "synthetic",
                                    "signal_type": "title",
                                    "key": f"Synthetic item {batch * 100 + index}",
                                    "classification": "example",
                                }
                                for index in range(100)
                            ],
                        }
                    ),
                    {"kind": "internal", "role": "system"},
                    runtime.clock.now_utc(),
                )
        await runtime._commit(staged)
        assert runtime.state.active_revision == 11
        assert len(runtime.config.contracts) == 100 and len(runtime.config.sources) == 90
        assert (
            sum(len(row["entries"]) for row in runtime.state.tables["catalog_head"].values())
            == 11200
        )
        return runtime, config
    except BaseException:
        await runtime.stop()
        raise


def ha_states(runtime):
    now = runtime.clock.now_utc().isoformat()
    return {
        f"binary_sensor.synthetic_{index}": {
            "state": True,
            "attributes": {},
            "last_changed": now,
            "last_updated": now,
            "last_reported": now,
        }
        for index in range(90)
    }


async def wait_for(predicate, seconds=10):
    async with asyncio.timeout(seconds):
        while not predicate():
            await SystemClock().sleep(0.005)


def projection_cache():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "synthetic_projection", Path(__file__).parents[1] / "custom_components/traber/projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Projection()


class LoadProbe:
    def __init__(self, runtime, fake, store):
        self.runtime, self.fake, self.store = runtime, fake, store
        self.latest = {}
        self.expected_events = {}
        self.committed_events = {}
        self.latencies = []
        self.availability = []
        self.operations = {}
        self.commit_sizes = []
        self.commit_timings = []
        self.write = store.commit

    async def commit(self, state):
        rows = list(state.tables["source_observation_history"].values())
        await self.write(state)
        self.commit_timings.append(dict(self.store.last_commit_timing))
        now = self.runtime.clock.now_utc()
        if rows:
            self.commit_sizes.append(len(rows))
        for row in rows:
            stamp = row.get("ha_time_fired")
            if stamp is None:
                continue
            from datetime import datetime

            self.latencies.append((now - datetime.fromisoformat(stamp)).total_seconds())
            if row["source_id"] in self.runtime._structure(self.runtime.config).protected:
                self.committed_events.setdefault(row["source_id"], []).append(
                    (stamp, row["value_raw"])
                )

    async def emit(self, index):
        source = index % 90
        key = f"source.synthetic{source}"
        now = self.runtime.clock.now_utc().isoformat().replace("+00:00", "Z")
        value = bool((index // 90) % 2)
        entity = f"binary_sensor.synthetic_{source}"
        state = {
            "state": value,
            "attributes": {},
            "last_changed": now,
            "last_updated": now,
            "last_reported": now,
        }
        self.fake.states[entity] = state
        self.latest[key] = (now, value)
        if key in self.runtime._structure(self.runtime.config).protected:
            self.expected_events.setdefault(key, []).append((now, value))
        await self.fake.emit(
            {"kind": "changed", "entity_id": entity, "new_state": state, "time_fired": now}
        )

    def current(self):
        for source, (stamp, value) in self.latest.items():
            row = self.runtime.state.tables["source_observation_current"].get(
                "binding." + source.removeprefix("source."), {}
            )
            if (row.get("ha_time_fired"), row.get("value_raw")) != (stamp, value):
                return False
        return self.committed_events == self.expected_events

    async def wait(self, seconds=10):
        try:
            await wait_for(self.current, seconds)
        except TimeoutError:
            current = self.runtime.state.tables["source_observation_current"]
            mismatches = [
                {
                    "expected": expected,
                    "actual": (
                        current.get("binding." + source.removeprefix("source."), {}).get(
                            "ha_time_fired"
                        ),
                        current.get("binding." + source.removeprefix("source."), {}).get(
                            "value_raw"
                        ),
                    ),
                }
                for source, expected in self.latest.items()
                if (
                    current.get("binding." + source.removeprefix("source."), {}).get(
                        "ha_time_fired"
                    ),
                    current.get("binding." + source.removeprefix("source."), {}).get("value_raw"),
                )
                != expected
            ]
            raise AssertionError(
                {
                    "mismatch_count": len(mismatches),
                    "examples": mismatches[:2],
                    "expected_event_count": sum(map(len, self.expected_events.values())),
                    "committed_event_count": sum(map(len, self.committed_events.values())),
                }
            ) from None

    def assert_transport(self):
        assert len(self.fake.connections) == 1
        assert not self.runtime.resubscribe.is_set()
        assert self.availability and all(self.availability)
        assert self.fake.projection_cache.available and len(self.fake.projection_cache.rows) == 100


@asynccontextmanager
async def live_bridge(runtime, aiohttp_server, states=None):
    fake = FakeHA(states or ha_states(runtime), v2=True, projection=True)
    fake.projection_cache = projection_cache()
    server = await aiohttp_server(fake.application())
    probe = LoadProbe(runtime, fake, runtime.store)
    runtime.store.commit = probe.commit

    async def tick():
        while True:
            runtime.tick()
            await runtime.clock.sleep(1)

    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        tasks = [
            asyncio.create_task(adapter.run()),
            asyncio.create_task(adapter.projection_worker()),
            asyncio.create_task(tick()),
        ]
        try:
            await wait_for(
                lambda: (
                    runtime.first_snapshot
                    and len(fake.projection_cache.rows) == 100
                    and all(row["status"] == "valid" for row in fake.projection_cache.rows.values())
                )
            )
            fake.projection_cache.listeners.add(
                lambda: probe.availability.append(
                    fake.projection_cache.available
                    and len(fake.projection_cache.rows) == 100
                    and all(
                        row["status"] != "unavailable"
                        for row in fake.projection_cache.rows.values()
                    )
                )
            )
            probe.availability.append(True)
            probe.latencies.clear()
            probe.commit_sizes.clear()
            probe.commit_timings.clear()
            yield probe
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            runtime.store.commit = probe.write


async def genuine_load(aiohttp_server, store=None):
    runtime, config = await scale_runtime(store)
    try:
        changed = copy.deepcopy(config)
        changed["contracts"][0]["display_name"] = "Synthetic review label"
        draft = await runtime.submit("draft_create", {"config": changed})
        package = await runtime.submit("portable_export", {"expected_active_revision": 11})
        async with live_bridge(runtime, aiohttp_server) as probe:
            started = perf_counter()

            async def administration():
                while perf_counter() - started < 60:
                    for operation in (
                        "validate",
                        "draft_preview",
                        "activation_prepare",
                        "portable_preview",
                    ):
                        before = perf_counter()
                        if operation == "validate":
                            await runtime.submit("validate", draft["config"])
                        elif operation == "draft_preview":
                            preview = await runtime.submit(
                                operation, {"draft_id": draft["draft_id"]}
                            )
                        elif operation == "activation_prepare":
                            plan = await runtime.submit("management_snapshot")
                            plan.operation = "activate"
                            await asyncio.to_thread(
                                prepare,
                                runtime,
                                plan,
                                {
                                    "draft_id": draft["draft_id"],
                                    "draft_version": draft["draft_version"],
                                    "request_id": str(uuid4()),
                                    "expected_active_revision": 11,
                                    "comparison_digest": preview["comparison_digest"],
                                    "confirmations": preview["required_confirmations"],
                                },
                                asyncio.get_running_loop(),
                            )
                        else:
                            await runtime.submit(operation, {"package": package})
                        probe.operations.setdefault(operation, []).append(perf_counter() - before)
                    await runtime.clock.sleep(0.25)

            admin = asyncio.create_task(administration())
            try:
                for index in range(1200):
                    await runtime.clock.sleep(max(0, started + index / 20 - perf_counter()))
                    await probe.emit(index)
                await admin
                await probe.wait()
                assert perf_counter() - started >= 59.9
                assert set(probe.operations) == {
                    "validate",
                    "draft_preview",
                    "activation_prepare",
                    "portable_preview",
                }
                probe.assert_transport()
                print(
                    json.dumps(
                        {
                            "synthetic": True,
                            "store": type(store).__name__ if store is not None else "MemoryStore",
                            "sent": 1200,
                            "latency_p95_s": percentile(probe.latencies, 0.95),
                            "commit_phases_mean": {
                                key: sum(item.get(key, 0) for item in probe.commit_timings)
                                / len(probe.commit_timings)
                                for key in (
                                    "commit_ms",
                                    "snapshot_ms",
                                    "changes_ms",
                                    "written_rows",
                                )
                            },
                            "admin_counts": {
                                key: len(value) for key, value in probe.operations.items()
                            },
                            "admin_max_s": {
                                key: max(value) for key, value in probe.operations.items()
                            },
                        }
                    )
                )
                assert percentile(probe.latencies, 0.95) < 1, percentile(probe.latencies, 0.95)
            finally:
                admin.cancel()
                await asyncio.gather(admin, return_exceptions=True)
    finally:
        await runtime.stop()


async def test_live_size_twenty_per_second_for_sixty_seconds_with_real_administration(
    aiohttp_server,
    caplog,
):
    # Benni's replacement criteria, #100 Go 03.11.2026: end states and complete
    # protected event sequences, rather than the count of coalescible values.
    caplog.set_level("INFO", logger="core_contracts")
    try:
        await genuine_load(aiohttp_server)
    except AssertionError:
        maximum = {}
        for record in caplog.records:
            if record.msg == "operation_timing":
                key = record.operation
                if record.run_ms > maximum.get(key, {}).get("run_ms", -1):
                    maximum[key] = {
                        "run_ms": record.run_ms,
                        "db_ms": record.db_ms,
                        "phases_ms": getattr(record, "phases_ms", {}),
                    }
        print(json.dumps({"synthetic": True, "max_operation_timing": maximum}))
        raise


async def test_live_size_five_hundred_event_burst_finishes_within_ten_seconds(aiohttp_server):
    runtime, _ = await scale_runtime()
    try:
        async with live_bridge(runtime, aiohttp_server) as probe:
            started = perf_counter()
            for index in range(500):
                await probe.emit(index)
            await probe.wait(seconds=max(0, 10 - (perf_counter() - started)))
            elapsed = perf_counter() - started
            assert elapsed <= 10
            probe.assert_transport()
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "burst": 500,
                        "latest_sources": len(probe.latest),
                        "protected_events": sum(map(len, probe.committed_events.values())),
                        "seconds": elapsed,
                    }
                )
            )
    finally:
        await runtime.stop()


async def test_thirty_second_cpu_worker_is_robust_without_latency_threshold(
    aiohttp_server, monkeypatch
):
    runtime, config = await scale_runtime()
    entered, release = threading.Event(), threading.Event()
    original = Planner.process

    async def delayed(self, operation, payload):
        if operation == "validate":
            entered.set()
            stop = perf_counter() + 30
            while perf_counter() < stop and not release.is_set():
                sum(range(5000))
        return await original(self, operation, payload)

    monkeypatch.setattr(Planner, "process", delayed)
    request = None
    try:
        async with live_bridge(runtime, aiohttp_server) as probe:
            request = asyncio.create_task(runtime.submit("validate", config))
            assert await asyncio.to_thread(entered.wait, 5)
            for index in range(500):
                await probe.emit(index)
            assert (await request)["valid"]
            await probe.wait()
            probe.assert_transport()
            if len(probe.latencies) < 500:
                assert any(
                    row["reason"] == "ingest_overflow"
                    for row in await runtime.store.rows("history_gap")
                )
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "cpu_seconds": 30,
                        "latest_sources": len(probe.latest),
                        "protected_events": sum(map(len, probe.committed_events.values())),
                        "connections": len(probe.fake.connections),
                    }
                )
            )
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await runtime.stop()


async def test_live_size_management_preparations_under_two_seconds():
    runtime, config = await scale_runtime()
    try:
        changed = copy.deepcopy(config)
        changed["contracts"][0]["display_name"] = "Synthetic changed label"
        draft = await runtime.submit("draft_create", {"config": changed})
        measurements = {}
        started = perf_counter()
        await runtime.submit("validate", draft["config"])
        measurements["validate_s"] = perf_counter() - started
        started = perf_counter()
        preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
        measurements["preview_s"] = perf_counter() - started
        plan = await runtime.submit("management_snapshot")
        plan.operation = "activate"
        started = perf_counter()
        await asyncio.to_thread(
            prepare,
            runtime,
            plan,
            {
                "draft_id": draft["draft_id"],
                "draft_version": draft["draft_version"],
                "request_id": str(uuid4()),
                "expected_active_revision": 11,
                "comparison_digest": preview["comparison_digest"],
                "confirmations": preview["required_confirmations"],
            },
            asyncio.get_running_loop(),
        )
        measurements["activation_prepare_s"] = perf_counter() - started
        assert all(value < 2 for value in measurements.values()), measurements
        print(json.dumps({"synthetic": True, **measurements}))
    finally:
        await runtime.stop()
