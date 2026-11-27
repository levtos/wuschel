"""A long-lived read-only worker process, with a fresh snapshot per job."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import UUID

import aiohttp
import asyncpg
from aiohttp import web
from yarl import URL

from . import wiring  # noqa: F401
from .api import API
from .clock import SystemClock
from .contract_types import type_registry
from .model import Model, canonical, checksum
from .operations import OperationsSettings
from .persistence import PostgresStore, State
from .portable_stage_work import PreparedPortableStage, prepare_stage
from .retention import RetentionPolicy, maintain_postgres, pinned_references
from .retention_work import prepare_retention
from .runtime import Runtime
from .setup import Connection
from .wiring import (
    Credentials,
    PreparedCatalogCleanup,
    PreparedCatalogWrite,
    PreparedClassRollback,
    PreparedScopeWrite,
    PreparedTitlePage,
    prepare_work_catalog_write,
    prepare_work_cleanup,
    prepare_work_rollback,
    prepare_work_scope,
    prepare_work_title_apply,
    prepare_work_title_stage,
    read_titles,
)
from .work_artifacts import load_plan
from .work_capacity import effective_cpu_count
from .work_process import FRAME_LIMIT, exit_with_parent, read_frame, write_frame
from .work_routes import WRITE_PLANS, work_route

READ_OPERATIONS = frozenset(
    {
        "portable_export",
        "portable_preview",
        "title_preview",
        "title_sync_preview",
        "catalog_preview",
        "catalog_care_preview",
        "catalog_scope_preview",
        "catalog_cleanup_preview",
        "catalog_cleanup_snapshot",
        "draft_preview",
        "validate",
        "retention_preview",
    }
)


class WorkerStore(PostgresStore):
    async def maintain_history(
        self, state: State, now: datetime, policy: RetentionPolicy, *, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run or not isinstance(self.pool, asyncpg.Connection):
            raise RuntimeError("work_read_only")
        return await maintain_postgres(
            self.pool, now, policy, pinned_references(state), dry_run=True
        )


class WorkerRuntime(Runtime):
    service_snapshot: dict[str, Any] | None = None
    prepared_work: Model | None = None
    job_id: UUID
    retention_scheduled = False

    def service_state(self) -> dict[str, Any]:
        return (
            self.service_snapshot if self.service_snapshot is not None else super().service_state()
        )

    async def submit(self, operation: str, payload: Any = None) -> Any:
        if operation == "portable_stage":
            result, self.prepared_work = await prepare_stage(self, payload)
            return result
        if operation == "title_stage":
            result = await prepare_work_title_stage(self, payload)
            if isinstance(result, PreparedPortableStage):
                self.prepared_work = result
                return result.draft
            return result
        if operation == "retention_run":
            connection = getattr(self.store, "pool", None)
            if not isinstance(connection, asyncpg.Connection):
                raise RuntimeError("work_read_only")
            prepared = await prepare_retention(
                self,
                connection,
                self.job_id,
                scheduled=self.retention_scheduled,
            )
            self.prepared_work = prepared
            return prepared.batch.receipt(dry_run=False)
        if operation in {"catalog_write", "catalog_care_write"}:
            result = await prepare_work_catalog_write(self, operation, payload)
            if isinstance(result, PreparedCatalogWrite):
                self.prepared_work = result
                return result.result
            return result
        if operation == "catalog_cleanup_apply":
            result = await prepare_work_cleanup(self, payload)
            if isinstance(result, PreparedCatalogCleanup):
                self.prepared_work = result
                return result.result
            return result
        if operation == "title_sync_apply":
            result = await prepare_work_title_apply(self, payload)
            if isinstance(result, PreparedTitlePage):
                self.prepared_work = result
                return result.result
            return result
        if operation == "catalog_scope_apply":
            result = await prepare_work_scope(self, payload)
            if isinstance(result, PreparedScopeWrite):
                self.prepared_work = result
                return result.result
            return result
        if operation == "rollback":
            result = await prepare_work_rollback(self, payload)
            if isinstance(result, PreparedClassRollback):
                self.prepared_work = result
                return {}
            return result
        if operation not in READ_OPERATIONS:
            raise RuntimeError("work_read_only")
        return await self.process(operation, payload)


async def run(
    metadata: dict[str, Any],
    body: BinaryIO,
    configuration: dict[str, Any],
    directory: Path,
) -> None:
    method, path = metadata["method"], metadata["path"]
    if not work_route(method, path):
        raise ValueError("work_route_invalid")
    database = Connection.model_validate(configuration["connection"])
    settings = OperationsSettings.model_validate(configuration["operations"])
    plan_kind = {
        "catalogs/cleanup/preview": "catalog_cleanup",
        "title-sync/preview": "title_sync",
    }.get(path)
    key = None
    titles: dict[str, Any] | None = None
    titles_error: ValueError | None = None
    if plan_kind is not None:
        try:
            key = json.load(body).get("plan_id")
        except AttributeError, UnicodeError, ValueError:
            pass
        finally:
            body.seek(0)
    if plan_kind == "title_sync" and not key:
        try:
            titles = await read_titles(
                database,
                Credentials.model_validate(configuration.get("title_classifier", {})),
                operations=settings,
            )
        except ValueError as error:
            titles_error = error

    connection = await asyncpg.connect(
        database.dsn(),
        ssl=database.tls(),
        timeout=settings.database_connect_timeout_s,
        command_timeout=settings.database_command_timeout_s,
        server_settings={
            "application_name": "wuschel_work_reader",
            "default_transaction_read_only": "on",
            "statement_timeout": str(int(settings.database_command_timeout_s * 1000)),
            "lock_timeout": "500",
            "idle_in_transaction_session_timeout": str(
                int(settings.database_idle_transaction_timeout_s * 1000)
            ),
        },
    )
    try:
        store = WorkerStore("", directory, operations=settings)
        store.pool = connection
        runtime = WorkerRuntime(store, SystemClock(), type_registry(), operations=settings)
        runtime.persistence = runtime.confirmed = True
        runtime.installation_id = configuration["installation_id"]
        runtime.job_id = UUID(metadata["job_id"])
        runtime.retention_scheduled = metadata["actor"]["kind"] == "internal"
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            tables = (
                {"registry_revision"} if method == "GET" and path.startswith("history/") else None
            )
            runtime.state = await store.load_read_state(connection, tables=tables)
            plans = (
                runtime.title_sync_plans
                if plan_kind == "title_sync"
                else runtime.catalog_cleanup_plans
            )
            if plan_kind == "catalog_cleanup" and isinstance(key, str):
                plan = await load_plan(connection, plan_kind, key, checksum(metadata["actor"]))
                if plan:
                    plans[key] = plan
            if metadata_socket := configuration.get("metadata_socket"):

                async def discover(**_: Any) -> list[dict[str, Any]]:
                    async with (
                        aiohttp.ClientSession(
                            connector=aiohttp.UnixConnector(path=metadata_socket),
                            timeout=aiohttp.ClientTimeout(total=settings.http_timeout_s),
                        ) as session,
                        session.get("http://localhost/entities") as response,
                    ):
                        response.raise_for_status()
                        return cast(list[dict[str, Any]], await response.json())

                runtime.entity_discovery = discover
            api = API(runtime, None, runtime.installation_id)
            if plan_kind == "title_sync":

                async def title_reader() -> dict[str, Any]:
                    if titles_error is not None:
                        raise titles_error
                    return titles or {}

                api.title_reader = title_reader
            if isinstance(metadata.get("service"), dict):
                runtime.service_snapshot = metadata["service"]
            app = api.application(work_actor=metadata["actor"])
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            try:
                socket = directory / "job.sock"
                socket.unlink(missing_ok=True)
                await web.UnixSite(runner, str(socket)).start()
                url = URL("http://localhost").with_path("/api/v1/" + path)
                async with aiohttp.ClientSession(
                    connector=aiohttp.UnixConnector(path=str(socket)),
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as client:
                    data = body if method != "GET" else None
                    async with client.request(
                        method,
                        url.with_query(metadata["query"]),
                        headers=metadata["headers"],
                        data=data,
                    ) as response:
                        plan_info = None
                        artifact = None
                        if response.status < 400 and plan_kind is not None and plans:
                            key, artifact = next(iter(plans.items()))
                            plan_info = (plan_kind, key)
                        commit = None
                        if response.status < 400 and runtime.prepared_work is not None:
                            artifact = runtime.prepared_work.model_dump(mode="json")
                            commit = WRITE_PLANS[method, path]
                        write_frame(
                            sys.stdout.buffer,
                            canonical(
                                {"status": response.status, "plan": plan_info, "commit": commit}
                            ).encode(),
                        )
                        async for part in response.content.iter_chunked(FRAME_LIMIT):
                            write_frame(sys.stdout.buffer, part)
                        write_frame(sys.stdout.buffer, b"")
                        if artifact is not None:
                            encoded = canonical(artifact).encode()
                            for start in range(0, len(encoded), FRAME_LIMIT):
                                write_frame(sys.stdout.buffer, encoded[start : start + FRAME_LIMIT])
                            write_frame(sys.stdout.buffer, b"")
            finally:
                await runner.cleanup()
    finally:
        await connection.close()


async def serve(configuration: dict[str, Any], directory: Path) -> None:
    source = sys.stdin.buffer
    output = sys.stdout.buffer
    write_frame(output, b"ready")
    while True:
        try:
            metadata = json.loads(await asyncio.to_thread(read_frame, source))
            with tempfile.TemporaryFile(mode="w+b", dir=directory) as body:
                while part := await asyncio.to_thread(read_frame, source):
                    body.write(part)
                body.seek(0)
                await run(metadata, cast(BinaryIO, body), configuration, directory)
        except EOFError:
            return
        except (
            OSError,
            ValueError,
            RuntimeError,
            asyncpg.PostgresError,
            aiohttp.ClientError,
        ) as error:
            # A partial reply cannot be replaced. Exit so the coordinator records
            # a crash rather than interpreting two different response envelopes.
            sys.stderr.write(type(error).__name__ + "\n")
            raise SystemExit(2) from None


def main() -> None:
    exit_with_parent(int(sys.argv[1]))
    if sys.platform == "linux":
        try:
            cpus = sorted(os.sched_getaffinity(0))
            if len(cpus) == effective_cpu_count() and len(cpus) > 1:
                os.sched_setaffinity(0, cpus[1:])
            os.nice(5)
        except OSError:
            pass
    configuration = json.loads(read_frame(sys.stdin.buffer))
    # The coordinator owns this private directory and reaps it with the process.
    asyncio.run(serve(configuration, Path(sys.argv[2])))


if __name__ == "__main__":
    main()
