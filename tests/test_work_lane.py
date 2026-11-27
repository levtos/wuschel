"""The work process stays outside the serial writer and preserves its receipts."""

import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import asyncpg
import pytest

from core_contracts import slots
from core_contracts.api import API
from core_contracts.clock import SystemClock
from core_contracts.operations import OperationsSettings
from core_contracts.persistence import PostgresStore
from core_contracts.security import Tokens
from core_contracts.work_capacity import READ_CONNECTION_LIMIT, WorkCapacity
from core_contracts.work_client import WorkClient
from core_contracts.work_routes import read_route
from core_contracts.work_worker import READ_OPERATIONS, WorkerRuntime

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


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
        "catalog_cleanup_preview",
        "catalog_cleanup_snapshot",
        "catalog_scope_apply",
        "catalog_write",
        "draft_complete",
        "draft_reconcile",
        "portable_stage",
        "retention_run",
        "title_stage",
        "title_sync_apply",
        "title_sync_preview",
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
async def test_work_survives_pause_and_isolates_owner(
    database, runtime, config, tmp_path, aiohttp_client
):
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
        response = await client.post("/api/v1/registry/validate", headers=headers, json=config)
        assert response.status == 202
        location = "/api/v1/" + response.headers["Location"]
        receipt = await response.json()
        assert receipt["phase"] == "waiting"
        stored = await store._writer().fetchrow(
            "SELECT phase, owner FROM cc_work_job WHERE id=$1", UUID(receipt["id"])
        )
        assert stored is not None and stored["owner"] == "token:admin"
        assert (await client.get("/api/v1/registry/drafts", headers=headers)).status == 200

        work.set_pressure("normal")
        async with asyncio.timeout(30):
            while (result := await client.get(location + "/result", headers=headers)).status == 202:
                await clock.sleep(0.15)
        assert result.status == 200 and (await result.json())["valid"]

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
