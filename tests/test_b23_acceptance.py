"""Mandatory live-size acceptance, entirely synthetic and isolated from HA."""

import asyncio
import copy
import json
import threading
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


async def scale_runtime():
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
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
                "source_type": "boolean",
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


async def test_live_size_fifty_per_second_during_thirty_second_management(
    aiohttp_server, monkeypatch
):
    runtime, config = await scale_runtime()
    fake = FakeHA(ha_states(runtime), v2=True, projection=True)
    server = await aiohttp_server(fake.application())
    entered, release = threading.Event(), threading.Event()
    original_plan = Planner.process
    original_process = runtime.process
    latency, statuses = [], []

    async def delayed(self, operation, payload):
        if operation == "validate":
            entered.set()
            stop = perf_counter() + 30
            while perf_counter() < stop and not release.is_set():
                sum(range(5000))
        return await original_plan(self, operation, payload)

    async def observed(operation, payload):
        result = await original_process(operation, payload)
        if operation == "observation":
            latency.append((runtime.clock.now_utc() - payload.ha_time_fired).total_seconds())
            statuses.append(
                all(
                    row["fields"]["value"]["status"] == "valid"
                    for row in runtime.state.tables["contract_state_current"].values()
                )
            )
        return result

    monkeypatch.setattr(Planner, "process", delayed)
    monkeypatch.setattr(runtime, "process", observed)
    tasks = []
    request = None
    try:
        async with aiohttp.ClientSession() as session:
            adapter = BridgeAdapter(
                runtime, session, str(server.make_url("/websocket")), "synthetic"
            )
            tasks = [
                asyncio.create_task(adapter.run()),
                asyncio.create_task(adapter.projection_worker()),
            ]
            await wait_for(
                lambda: (
                    runtime.ready
                    and all(
                        row["fields"]["value"]["status"] == "valid"
                        for row in runtime.state.tables["contract_state_current"].values()
                    )
                )
            )
            request = asyncio.create_task(runtime.submit("validate", config))
            assert await asyncio.to_thread(entered.wait, 5)
            started = perf_counter()
            for index in range(1500):
                await runtime.clock.sleep(max(0, started + index / 50 - perf_counter()))
                now = runtime.clock.now_utc().isoformat()
                entity = f"binary_sensor.synthetic_{index % 90}"
                state = {
                    "state": bool((index // 90) % 2),
                    "attributes": {},
                    "last_changed": now,
                    "last_updated": now,
                    "last_reported": now,
                }
                fake.states[entity] = state
                await fake.emit(
                    {"kind": "changed", "entity_id": entity, "new_state": state, "time_fired": now}
                )
            assert (await request)["valid"]
            print(
                json.dumps(
                    {
                        "processed": len(latency),
                        "queue": runtime.queue.qsize(),
                        "coalesced": len(runtime.latest),
                        "generated_duration_s": perf_counter() - started,
                        "connections": len(fake.connections),
                        "p95_so_far_s": percentile(latency, 0.95) if latency else None,
                    }
                )
            )
            await wait_for(lambda: len(latency) == 1500)
            duration = perf_counter() - started
            assert duration < 32
            assert percentile(latency, 0.95) < 1
            assert all(statuses)
            assert len(fake.connections) == 1
            assert not runtime.resubscribe.is_set() and not runtime.overflow
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "observations": len(latency),
                        "duration_s": duration,
                        "observations_per_s": len(latency) / duration,
                        "latency_p95_s": percentile(latency, 0.95),
                        "connections": len(fake.connections),
                    }
                )
            )
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
