"""Opt-in process acceptance using a dedicated PostgreSQL database and synthetic Traber."""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from time import perf_counter, process_time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import aiohttp
import asyncpg
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.app import apply_work
from core_contracts.clock import SystemClock
from core_contracts.contract_types import type_registry
from core_contracts.extensions.tc_migration.title_sync import current_mapping
from core_contracts.model import canonical, checksum
from core_contracts.operations import OperationsSettings
from core_contracts.persistence import PostgresStore
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.truth_latency import TruthLatency
from core_contracts.work_client import WorkClient
from core_contracts.work_journal import ARTIFACT_CHUNK
from core_contracts.work_process import WorkProcess
from dev.fake_ha import FakeHA
from dev.truth_lane_profile import Timings, distribution, registry, seed
from tests.conftest import activation_body
from tests.test_b23_acceptance import projection_cache, wait_for
from tests.test_beta_media_import import document as tc_document
from tests.test_postgres import MIGRATIONS
from tests.test_postgres import database as database
from tests.test_title_sync import ACTOR, inspect, mapping, observed

SECONDS = int(os.environ.get("WUSCHEL_SYNTHETIC_ACCEPTANCE_SECONDS", "0"))
SCALE = int(os.environ.get("WUSCHEL_SYNTHETIC_ACCEPTANCE_SCALE", "2"))
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        sys.platform != "linux" or SECONDS <= 0, reason="manual synthetic process acceptance"
    ),
]


class AcceptanceTimings(Timings):
    def __init__(self):
        super().__init__()
        self.peak = {}

    def emit(self, record):
        super().emit(record)
        if record.msg == "event_loop_stall" and record.lag_ms > self.peak.get("lag_ms", 0):
            self.peak = {"lag_ms": record.lag_ms, "activities": record.activities}


class RecordedTruthLatency(TruthLatency):
    def __init__(self):
        super().__init__()
        self.all_acknowledgments_ms = []

    def acknowledge(self, sequence, now):
        for queued_sequence, queued_at in self.pending:
            if queued_sequence > sequence:
                break
            self.all_acknowledgments_ms.append(max(0, now - queued_at) * 1000)
        super().acknowledge(sequence, now)


def config(scale):
    result = registry(scale)
    catalog = tc_document({"catalogs": []})["catalogs"][0]
    result["catalogs"].append(catalog)
    result["sources"][1]["source_type"] = "event"
    field = {"type": "bool", "required": True, "physical_state": False}
    for source in result["sources"][1:]:
        source["value_schema"] = field
    result["definitions"] = [
        {
            "type_id": "fixture.echo",
            "type_version": 1,
            "inputs": [{"name": "a", "schema": field}],
            "schema": {"fields": {"value": field}, "available_projection": ["value"]},
            "outputs": {"value": {"op": "input", "name": "a"}},
        }
    ]
    sources = result["sources"][1:]
    for index, contract in enumerate(result["contracts"]):
        contract.update(
            type_id="fixture.echo",
            ha_projection=True,
            inputs=[{"name": "a", "ref": sources[index % len(sources)]["source_id"]}],
        )
    return result


def worker_config(dsn, operations, installation_id):
    parts = urlsplit(dsn)
    return {
        "connection": {
            "host": parts.hostname,
            "port": parts.port or 5432,
            "database": unquote(parts.path.removeprefix("/")),
            "user": unquote(parts.username or ""),
            "password": unquote(parts.password or ""),
            "sslmode": "disable",
        },
        "operations": operations.model_dump(mode="json"),
        "installation_id": installation_id,
    }


async def store_title_snapshot(dsn, runtime, title_plan):
    key = title_plan["plan_id"]
    job = uuid4()
    row = runtime.title_sync_plans[key]
    # TC's read connector has separate TLS and permission tests. Load its
    # synthetic output through the same durable artifact format used in production.
    payload = canonical(row).encode()
    connection = await asyncpg.connect(dsn, ssl=False)
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO cc_work_job(id,owner,operation,request_hash,metadata,phase)"
                " VALUES($1,'token:admin','title-sync/preview','synthetic','{}','finished')",
                job,
            )
            await connection.executemany(
                "INSERT INTO cc_work_artifact(job_id,kind,part,content) VALUES($1,'plan',$2,$3)",
                [
                    (job, index // ARTIFACT_CHUNK, payload[index : index + ARTIFACT_CHUNK])
                    for index in range(0, len(payload), ARTIFACT_CHUNK)
                ],
            )
            await connection.execute(
                "INSERT INTO cc_work_plan(kind,plan_key,owner,job_id) VALUES('title_sync',$1,$2,$3)",
                key,
                checksum(ACTOR),
                job,
            )
    finally:
        await connection.close()


async def accepted(client, headers, clock, method, path, body=None, *, result_status=200):
    request_headers = {**headers, "Prefer": "respond-async"}
    async with client.request(
        method, "/api/v1/" + path, headers=request_headers, json=body
    ) as response:
        assert response.status == 202, (path, response.status, await response.text())
        location = "/api/v1/" + response.headers["Location"]
        receipt = await response.json()
        assert receipt["phase"] in {"waiting", "running", "finished"}
    async with asyncio.timeout(180):
        while True:
            async with client.get(location + "/result", headers=headers) as response:
                value = await response.json()
                if response.status != 202:
                    assert response.status == result_status, (
                        path,
                        response.status,
                        value.get("error"),
                    )
                    return value
            await clock.sleep(0.2)


async def migrate(client, headers, runtime):
    apply = {"expected_active_revision": runtime.state.active_revision}
    started = perf_counter()
    preview = await accepted(
        client, headers, runtime.clock, "POST", "catalogs/classes/migration", apply
    )
    assert preview["preview"].get("can_stage") is not False
    assert not preview["preview"]["unresolved"]
    preview_ms = (perf_counter() - started) * 1000
    stage = {"package": preview["package"], "resolutions": preview["resolutions"]}
    failed = await accepted(
        client,
        headers,
        runtime.clock,
        "POST",
        "portable/stage",
        {**stage, "plan_digest": "wrong", "request_id": str(uuid4())},
        result_status=409,
    )
    assert failed["error"] == "import_plan_conflict"
    assert runtime.state.active_revision == apply["expected_active_revision"]
    draft = await accepted(
        client,
        headers,
        runtime.clock,
        "POST",
        "portable/stage",
        {**stage, "plan_digest": preview["preview"]["plan_digest"], "request_id": str(uuid4())},
    )
    path = f"/api/v1/registry/drafts/{draft['draft_id']}"
    activation = await activation_body(client, path, headers, draft["draft_version"])
    # Exercise the b32 writer during sustained input. The production HTTP
    # wrapper can reject an aged speculative snapshot before this operation.
    result = await runtime._enqueue(
        "activate", {**activation, "draft_id": draft["draft_id"], "_actor": ACTOR}
    )
    assert result["revision"] == apply["expected_active_revision"] + 1
    # The failed preparation cannot cause a second activation. An actual HTTP
    # retry of the successful activation returns its durable receipt.
    async with client.post(path + "/activate", headers=headers, json=activation) as response:
        assert response.status == 200
        assert (await response.json())["revision"] == result["revision"]
    assert runtime.state.active_revision == result["revision"]
    return preview_ms


async def reconcile(dsn, client, headers, runtime):
    source = await inspect(
        runtime, [observed(f"Synthetic imported {i:03}") for i in range(75)], mapping()
    )
    await store_title_snapshot(dsn, runtime, source)
    plan = await accepted(
        client,
        headers,
        runtime.clock,
        "POST",
        "title-sync/preview",
        {
            "expected_active_revision": runtime.state.active_revision,
            "plan_id": source["plan_id"],
            "mapping": mapping(),
            "expected_mapping_version": current_mapping(runtime.state)["version"],
        },
    )
    assert plan["can_apply"], plan["error_groups"]
    applied = await accepted(
        client,
        headers,
        runtime.clock,
        "POST",
        "title-sync/apply",
        {
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "request_id": str(uuid4()),
        },
    )
    assert applied["changed"] > 0


async def check_worker_failure(runtime, client, headers, coordinator):
    def current_children():
        pid = coordinator.process.pid
        path = Path(f"/proc/{pid}/task/{pid}/children")
        return [int(child) for child in path.read_text(encoding="ascii").split()]

    before = runtime.state.active_revision, dict(runtime.state.tables["registry_draft"])
    for abort in (False, True):
        children = current_children()
        assert len(children) == 1, children
        worker = children[0]
        os.kill(worker, signal.SIGSTOP)
        try:
            async with client.post(
                "/api/v1/catalogs/classes/migration",
                headers={**headers, "Prefer": "respond-async"},
                json={"expected_active_revision": runtime.state.active_revision},
            ) as response:
                assert response.status == 202
                location = "/api/v1/" + response.headers["Location"]
            async with asyncio.timeout(15):
                while True:
                    async with client.get(location, headers=headers) as response:
                        current = await response.json()
                    if current["phase"] == "running":
                        break
                    await runtime.clock.sleep(0.05)
            async with asyncio.timeout(10):
                result = await accepted(
                    client, headers, runtime.clock, "GET", "history/query/contracts?limit=10"
                )
                assert "items" in result
            if abort:
                async with client.delete(location, headers=headers) as response:
                    assert response.status == 204
            else:
                os.kill(worker, signal.SIGKILL)
            terminal = "cancelled" if abort else "failed"
            async with asyncio.timeout(10):
                while True:
                    async with client.get(location, headers=headers) as response:
                        current = await response.json()
                    if current["phase"] in {"cancelled", "failed"}:
                        assert current["phase"] == terminal
                        break
                    await runtime.clock.sleep(0.05)
            async with asyncio.timeout(5):
                while True:
                    async with client.get("/api/v1/system/state", headers=headers) as response:
                        state = await response.json()
                    jobs = state["work"]["jobs"]
                    if any(job["id"] == current["id"] and job["phase"] == terminal for job in jobs):
                        break
                    await runtime.clock.sleep(0.25)
        finally:
            if worker in current_children():
                try:
                    os.kill(worker, signal.SIGCONT)
                except ProcessLookupError:
                    pass
        await wait_for(lambda: worker not in current_children())
    assert before == (runtime.state.active_revision, dict(runtime.state.tables["registry_draft"]))


async def test_double_inventory_acceptance(
    database, tmp_path, aiohttp_server, aiohttp_client, monkeypatch
):
    clock = SystemClock()
    operations = OperationsSettings(work_max_workers=4)
    store = PostgresStore(database, MIGRATIONS, tls=False, operations=operations)
    await store.open()
    runtime = Runtime(store, clock, type_registry(), operations=operations)
    runtime.installation_id = str(uuid4())
    await runtime.start()
    work = WorkClient(clock, apply=partial(apply_work, runtime))
    tokens = Tokens(tmp_path / "secrets")
    admin = {"Authorization": "Bearer " + tokens.values["admin"], "X-Authoring-Protocol": "2"}
    server = await aiohttp_client(
        API(runtime, tokens, runtime.installation_id, work=work).application()
    )
    tasks = []
    coordinators = []
    start_process = WorkProcess.start

    async def capture(module, configuration, *args, **kwargs):
        child = await start_process(module, configuration, *args, **kwargs)
        if module == "core_contracts.work_coordinator":
            coordinators.append(child)
        return child

    monkeypatch.setattr(WorkProcess, "start", capture)
    try:
        await seed(runtime, SCALE, document=config(SCALE))
        now = clock.now_utc().isoformat()
        entities = [
            binding.adapter.ha_entity_id
            for binding in runtime.config.bindings
            if binding.adapter.kind == "ha_state"
        ]
        states = {
            entity: {"state": False, "attributes": {}, "last_updated": now, "last_reported": now}
            for entity in entities
        }
        fake = FakeHA(states, v2=True, projection=True, record_requests=False)
        fake.projection_cache = projection_cache()
        ha = await aiohttp_server(fake.application())
        async with aiohttp.ClientSession() as session:
            adapter = BridgeAdapter(runtime, session, str(ha.make_url("/websocket")), "synthetic")
            tasks = [
                asyncio.create_task(
                    work.supervise(worker_config(database, operations, runtime.installation_id))
                ),
                asyncio.create_task(clock.run()),
                asyncio.create_task(adapter.run()),
                asyncio.create_task(adapter.projection_worker()),
                asyncio.create_task(runtime.measure_event_loop()),
            ]
            try:
                await asyncio.wait_for(work.ready.wait(), 30)
                await wait_for(
                    lambda: (
                        runtime.first_snapshot and len(fake.projection_cache.rows) == 100 * SCALE
                    )
                )
                assert len(coordinators) == 1
                await check_worker_failure(runtime, server, admin, coordinators[0])
                await exercise(runtime, work, server, admin, fake, entities, database)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await runtime.stop()
        await store.close()


def acceptance_schedule(seconds, scale):
    # ADR 0034's A1 profile, doubled in both steady state and ten-second bursts.
    schedule = []
    for second in range(seconds):
        rate = 25 * scale if 30 <= second % 300 < 40 else 5 * scale
        schedule.extend(second + event / rate for event in range(rate))
    return schedule


def window_measurements(lag, windows, start, schedule):
    measured = []
    unprotected_lag = [max(0, finish - target) * 1000 for target, finish in lag]
    for operation, begin, end in windows:
        overlaps = [max(0, min(finish, end) - max(target, begin)) * 1000 for target, finish in lag]
        unprotected_lag = [
            max(0, sample - overlap)
            for sample, overlap in zip(unprotected_lag, overlaps, strict=True)
        ]
        delayed = sum(begin <= start + offset < end for offset in schedule)
        measured.append(
            {
                "operation": operation,
                "duration_ms": round((end - begin) * 1000, 3),
                "max_loop_lag_ms": round(max(overlaps, default=0), 3),
                "events_due_during_window": delayed,
            }
        )
    return measured, distribution(unprotected_lag)


async def exercise(runtime, work, client, headers, fake, entities, dsn):
    start, cpu = perf_counter(), process_time()
    first_stamp = runtime.clock.now_utc()
    schedule = acceptance_schedule(SECONDS, SCALE)
    lag, windows = [], []
    expected, observed_events = [], []
    latest = {entity: False for entity in entities}
    entities_by_source = {
        binding.source_id: binding.adapter.ha_entity_id
        for binding in runtime.config.bindings
        if binding.adapter.kind == "ha_state"
    }

    def expected_value(contract):
        return latest[entities_by_source[contract.inputs[0].ref]]

    history_queries = 0
    store = runtime.store
    commit = store.commit
    timings = AcceptanceTimings()
    logger = logging.getLogger("core_contracts")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(timings)
    truth_latency = RecordedTruthLatency()
    runtime.truth_latency = truth_latency

    async def track_commit(state):
        frames = list(state.tables["source_observation_history"].values())
        await commit(state)
        observed_events.extend(
            (row["ha_time_fired"], row["value_raw"])
            for row in frames
            if row["ha_time_fired"] and row["source_id"] == "source.synthetic_1"
        )

    process = runtime.process

    async def track_process(operation, payload):
        begin = perf_counter()
        result = await process(operation, payload)
        if operation in {"activate", "rollback"}:
            windows.append((operation, begin, perf_counter()))
        return result

    async def heartbeat():
        while True:
            target = perf_counter() + 0.02
            await runtime.clock.sleep(0.02)
            lag.append((target, perf_counter()))

    async def operating_state():
        while True:
            runtime.tick()
            runtime.sample_load()
            work.set_pressure(runtime.load_guard.state)
            await runtime.clock.sleep(1)

    async def history():
        nonlocal history_queries
        while True:
            result = await accepted(
                client, headers, runtime.clock, "GET", "history/query/contracts?limit=10"
            )
            assert "items" in result
            history_queries += 1
            await runtime.clock.sleep(2)

    async def administration():
        preview_ms = await migrate(client, headers, runtime)
        await reconcile(dsn, client, headers, runtime)
        revision = runtime.state.active_revision
        result = await runtime._enqueue(
            "rollback", {"revision": revision, "expected_active_revision": revision}
        )
        assert result["revision"] == revision + 1
        return preview_ms

    runtime.process = track_process
    store.commit = track_commit
    routing = asyncio.create_task(history())
    tasks = (asyncio.create_task(heartbeat()), asyncio.create_task(operating_state()), routing)
    manage = asyncio.create_task(administration())
    try:
        for index, offset in enumerate(schedule):
            await runtime.clock.sleep(max(0, start + offset - perf_counter()))
            entity = entities[index % len(entities)]
            latest[entity] = not latest[entity]
            if routing.done():
                await routing
            if manage.done():
                await manage
            stamp = (first_stamp + timedelta(seconds=offset)).isoformat()
            state = {
                "state": latest[entity],
                "attributes": {},
                "last_updated": stamp,
                "last_reported": stamp,
            }
            fake.states[entity] = state
            if entity == entities_by_source["source.synthetic_1"]:
                expected.append((datetime.fromisoformat(stamp), latest[entity]))
            await fake.emit(
                {"kind": "changed", "entity_id": entity, "new_state": state, "time_fired": stamp}
            )
        await runtime.clock.sleep(max(0, start + SECONDS - perf_counter()))
        preview_ms = await manage
        await runtime.queue.join()
        await wait_for(
            lambda: len(observed_events) == len(expected) and fake.projection_cache.available,
            seconds=30,
        )
        parsed = [(datetime.fromisoformat(stamp), value) for stamp, value in observed_events]
        assert parsed == expected
        await wait_for(
            lambda: all(
                row.get("value") is expected_value(contract)
                for contract in runtime.config.contracts
                for row in [fake.projection_cache.rows.get(contract.contract_id, {})]
            )
        )
        contracts = runtime.snapshot()["contracts"]
        assert all(
            contracts[contract.contract_id]["fields"]["value"]["value"] is expected_value(contract)
            for contract in runtime.config.contracts
        )
        assert len(fake.connections) == 1 and not runtime.resubscribe.is_set()
        assert history_queries > 0
        measurements, outside = window_measurements(lag, windows, start, schedule)
        assert [window["operation"] for window in measurements] == ["activate", "rollback"]
        assert outside["max_ms"] <= 1000, outside
        assert perf_counter() - start >= SECONDS
        assert truth_latency.all_acknowledgments_ms
        truth = distribution(truth_latency.all_acknowledgments_ms)
        print(
            json.dumps(
                {
                    "elapsed_s": round(perf_counter() - start, 3),
                    "steady_event_rate_per_s": 5 * SCALE,
                    "burst_event_rate_per_s": 25 * SCALE,
                    "offered_event_rate_per_s": round(len(schedule) / SECONDS, 3),
                    "events_sent": len(schedule),
                    "protected_events": len(parsed),
                    "history_queries": history_queries,
                    "loop": distribution([max(0, end - target) * 1000 for target, end in lag]),
                    "loop_outside_activation_or_rollback": outside,
                    "registry_windows": measurements,
                    "migration_preview_ms": round(preview_ms, 3),
                    "truth_input_to_ack": truth,
                    "observation_block": distribution(timings.measurements["observation_block"]),
                    "tick": distribution(timings.measurements["tick"]),
                    "main_process_cpu_cores": round(
                        (process_time() - cpu) / (perf_counter() - start), 3
                    ),
                }
            )
        )
    finally:
        print(
            json.dumps(
                {
                    "partial_elapsed_s": round(perf_counter() - start, 3),
                    "loop": distribution([max(0, end - target) * 1000 for target, end in lag]),
                    "registry_windows": window_measurements(lag, windows, start, schedule)[0],
                    "load": runtime.load_guard.view(runtime.clock.monotonic()),
                    "history_queries": history_queries,
                    "loop_peak_activity": timings.peak,
                    "observed_events": len(observed_events),
                    "expected_events": len(expected),
                    "adapter_connections": len(fake.connections),
                    "operation": runtime.activities.operation(),
                    "projection_acks": len(truth_latency.all_acknowledgments_ms),
                }
            ),
            flush=True,
        )
        for task in (*tasks, manage):
            task.cancel()
        await asyncio.gather(*tasks, manage, return_exceptions=True)
        runtime.process = process
        store.commit = commit
        logger.removeHandler(timings)
        logger.setLevel(previous_level)
