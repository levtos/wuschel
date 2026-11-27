"""The work process stays outside the serial writer and preserves its receipts."""

import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import asyncpg
import pytest

from core_contracts import slots
from core_contracts.api import API, LOGICAL_ROUTES, PARAMETERIZED_ROUTES
from core_contracts.clock import SystemClock
from core_contracts.operations import OperationsSettings
from core_contracts.persistence import PostgresStore
from core_contracts.security import Tokens
from core_contracts.work_capacity import READ_CONNECTION_LIMIT, WorkCapacity
from core_contracts.work_client import WorkClient
from core_contracts.work_routes import job_route, read_route
from core_contracts.work_worker import READ_OPERATIONS, WorkerRuntime

from .conftest import activate
from .test_postgres import database as database

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_route_manifest_matches_core_and_extensions(runtime, tmp_path):
    local_core = {
        "bindings",
        "capabilities",
        "commands",
        "contracts",
        "definitions",
        "diagnostics",
        "editorial",
        "ha/contexts",
        "ha/entities",
        "info",
        "manual-restarts",
        "operations/projection",
        "operations/settings",
        "operations/settings/reset",
        "operations/wait",
        "reasons",
        "registry/active",
        "registry/active/export",
        "registry/drafts",
        "registry/revisions",
        "registry/rollback",
        "registry/schema",
        "snapshot",
        "sources",
        "sources/calendar-status",
        "system/state",
        "tokens",
        "tokens/rotate",
        "types",
        "work/jobs",
    }
    work_core = {
        "diagnostics/problems",
        "history/retention/preview",
        "history/retention/run",
        "history/rollups",
        "portable/export",
        "portable/preview",
        "portable/stage",
        "registry/freshness",
        "registry/freshness/proposals",
        "registry/validate",
    }
    assert LOGICAL_ROUTES == local_core | work_core
    assert {name for _, name in PARAMETERIZED_ROUTES} == {
        "contracts/{id}",
        "commands/{id}",
        "diagnostics/contracts/{id}",
        "manual-restarts/preview/{id}",
        "manual-sources/{id}/observations",
        "manual-sources/{id}/requests/{request_id}",
        "evidence/reference/{id}",
        "evidence/{id}",
        "history/{id}",
        "registry/revisions/{id}",
        "registry/activation-requests/{id}",
        "registry/continuations/{id}",
        "registry/drafts/{id}",
        "work/jobs/{id}",
    }
    app = API(runtime, Tokens(tmp_path / "secrets"), "synthetic").application()
    registered = {resource.canonical for resource in app.router.resources()}
    catalogues = {
        "catalogs",
        "catalogs/care",
        "catalogs/care/queue",
        "catalogs/classes/migration",
        "catalogs/classes/rollback",
        "catalogs/classes/outputs",
        "catalogs/care/inspect",
        "catalogs/care/export",
        "catalogs/care/detail",
        "catalogs/care/preview",
        "catalogs/care/write",
        "catalogs/scope/preview",
        "catalogs/scope/apply",
        "catalogs/cleanup/preview",
        "catalogs/cleanup/apply",
        "catalogs/entries",
        "catalogs/preview",
        "catalogs/entry",
        "catalogs/history",
        "catalogs/revision",
    }
    titles = {
        "title-sync/mapping",
        "title-sync/preview",
        "title-sync/page",
        "title-sync/cancel",
        "title-sync/diff",
        "title-sync/apply",
        "title-sync/requests",
        "title-import/preview",
        "title-import/stage",
    }
    assert registered == {"/api/v1/" + path for path in catalogues | titles} | {
        "/health/live",
        "/health/ready",
        "/api/v1/ws",
        "/api/v1/{path}",
    }
    assert not job_route("PUT", "work/jobs/../../pressure")


async def test_local_routes_and_loop_remain_responsive_with_a_busy_subprocess(
    runtime, config, tmp_path, aiohttp_client
):
    sources = [
        {
            **config["sources"][0],
            "source_id": f"source.synthetic_{index}",
            "physical_source_key": f"physical.synthetic_{index}",
        }
        for index in range(180)
    ]
    config["sources"] = sources
    config["bindings"] = [
        {
            **config["bindings"][0],
            "binding_id": f"binding.synthetic_{index}",
            "source_id": source["source_id"],
            "adapter": {"kind": "ha_state", "ha_entity_id": f"sensor.synthetic_{index}"},
        }
        for index, source in enumerate(sources)
    ]
    config["contracts"] = [
        {
            **config["contracts"][0],
            "contract_id": f"fixture.synthetic_{index}",
            "inputs": [{"name": "a", "ref": sources[index % len(sources)]["source_id"]}],
        }
        for index in range(200)
    ]
    await activate(runtime, config)
    tokens = Tokens(tmp_path / "secrets")
    client = await aiohttp_client(API(runtime, tokens, "synthetic").application())
    headers = {"Authorization": "Bearer " + tokens.values["admin"]}
    clock = asyncio.get_running_loop()
    lag = []

    async def meter():
        never = asyncio.Event()
        while True:
            before = clock.time()
            try:
                await asyncio.wait_for(never.wait(), 0.02)
            except TimeoutError:
                lag.append(clock.time() - before - 0.02)

    sampler = asyncio.create_task(meter())
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from time import monotonic\nend = monotonic() + 3\nwhile monotonic() < end:\n    pass\n",
    )
    try:
        for path in (
            "contracts",
            "snapshot",
            "registry/drafts",
            "system/state",
            "operations/settings",
        ):
            times = []
            for _ in range(20):
                started = clock.time()
                response = await client.get("/api/v1/" + path, headers=headers)
                assert response.status == 200
                await response.read()
                times.append(clock.time() - started)
            assert sorted(times)[18] <= 0.05, (path, times)
        assert child.returncode is None
        assert lag and max(lag) <= 1
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)


def test_work_pool_capacity_and_protection():
    capacity = WorkCapacity.for_cpus(4)
    assert (capacity.cpus, capacity.needed, capacity.maximum) == (4, 3, 6)
    assert capacity.slots("normal") == 6
    assert capacity.slots("angespannt") == 1
    assert capacity.slots("geschützt") == 0
    assert WorkCapacity.for_cpus(4, 4).maximum == 4
    assert WorkCapacity.for_cpus(1).maximum == 1
    assert WorkCapacity.for_cpus(16).normal_slots == READ_CONNECTION_LIMIT


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "diagnostics/problems"),
        ("GET", "history/query/contracts"),
        ("GET", "catalogs/care"),
        ("GET", "catalogs/classes/outputs"),
        ("POST", "registry/freshness/proposals"),
        ("POST", "registry/drafts/example/activation-preview"),
        ("POST", "portable/preview"),
    ],
)
def test_work_admission_is_explicit(method, path):
    assert read_route(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "contracts"),
        ("GET", "registry/drafts/example"),
        ("POST", "registry/drafts/example/activate"),
        ("POST", "registry/rollback"),
        ("POST", "operations/settings/reset"),
        ("POST", "history/query/contracts"),
        ("GET", "unknown"),
    ],
)
def test_truth_and_control_routes_are_local(method, path):
    assert not read_route(method, path)


def test_registered_operations_have_a_closed_classification():
    local = {
        "activate",
        "rollback",
        "audit",
        "catalog_revision",
        "catalog_tick",
        "draft_create",
        "draft_delete",
        "draft_update",
        "editorial_area_refresh",
        "editorial_put",
        "legacy_refresh_prepare",
        "management_commit",
        "management_snapshot",
        "manual_restart",
        "manual_restart_preview",
        "service_claim",
        "service_complete",
    }
    prepared = {
        "catalog_care_write",
        "catalog_cleanup_apply",
        "catalog_scope_apply",
        "catalog_write",
        "draft_complete",
        "draft_reconcile",
        "portable_stage",
        "portable_stage_prepared",
        "retention_run",
        "title_stage",
        "title_sync_apply",
    }
    assert not (local & prepared or local & READ_OPERATIONS or prepared & READ_OPERATIONS)
    assert set(slots.OPERATIONS) == local | prepared | READ_OPERATIONS
    assert set(slots.SUBMIT) == {
        "activate",
        "catalog_cleanup_preview",
        "draft_create",
        "draft_preview",
        "draft_update",
        "portable_export",
        "portable_preview",
        "rollback",
        "title_sync_preview",
        "validate",
    }
    assert set(slots.OPERATION_PREFIXES) == {"continuation_"}
    assert set(slots.SUBMIT_PREFIXES) == {"catalog_care_"}


async def test_worker_disallows_truth_write_before_dispatch(runtime):
    worker = WorkerRuntime(runtime.store, runtime.clock, runtime.types)
    with pytest.raises(RuntimeError, match="work_read_only"):
        await worker.submit("activate", {})
    with pytest.raises(RuntimeError, match="work_read_only"):
        await worker.submit("catalog_write", {})
    assert worker.queue.empty()


@pytest.mark.postgres
@pytest.mark.skipif(sys.platform != "linux", reason="Supervisor process IPC uses Unix sockets")
async def test_work_survives_pause_and_isolates_owner(database, runtime, tmp_path, aiohttp_client):
    database_config = urlsplit(database)
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    clock = SystemClock()
    clock_task = asyncio.create_task(clock.run())
    work = WorkClient(clock)
    tokens = Tokens(tmp_path / "secrets")
    client = await aiohttp_client(API(runtime, tokens, "synthetic", work=work).application())
    work.set_pressure("geschützt")
    task = asyncio.create_task(
        work.supervise(
            {
                "connection": {
                    "host": database_config.hostname,
                    "port": database_config.port or 5432,
                    "database": unquote(database_config.path.removeprefix("/")),
                    "user": unquote(database_config.username or ""),
                    "password": unquote(database_config.password or ""),
                    "sslmode": "disable",
                },
                "operations": OperationsSettings(work_max_workers=2).model_dump(mode="json"),
                "installation_id": "synthetic",
            }
        )
    )
    headers = {"Authorization": "Bearer " + tokens.values["admin"], "Prefer": "respond-async"}
    try:
        await asyncio.wait_for(work.ready.wait(), 30)
        response = await client.get("/api/v1/history/query/contracts", headers=headers)
        assert response.status == 202
        location = "/api/v1/" + response.headers["Location"]
        receipt = await response.json()
        assert receipt["phase"] == "waiting"
        stored = await store._writer().fetchrow(
            "SELECT phase, owner FROM cc_work_job WHERE id=$1", UUID(receipt["id"])
        )
        assert stored is not None and stored["owner"] == "token:admin"
        consumer = {"Authorization": "Bearer " + tokens.values["consumer"]}
        assert (await client.get(location, headers=consumer)).status == 403
        assert (await client.get("/api/v1/registry/drafts", headers=headers)).status == 200

        work.set_pressure("normal")
        async with asyncio.timeout(30):
            while (result := await client.get(location + "/result", headers=headers)).status == 202:
                await clock.sleep(0.15)
        assert result.status == 200 and (await result.json())["items"] == []

        protected = await asyncpg.connect(
            database, ssl=False, server_settings={"default_transaction_read_only": "on"}
        )
        try:
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await protected.execute("DELETE FROM cc_meta")
        finally:
            await protected.close()
    finally:
        task.cancel()
        clock_task.cancel()
        await asyncio.gather(task, clock_task, return_exceptions=True)
        await store.close()
