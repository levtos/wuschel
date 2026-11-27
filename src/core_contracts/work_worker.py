"""A long-lived read-only worker process, with a fresh snapshot per job."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

import aiohttp
import asyncpg
from aiohttp import web
from yarl import URL

from . import wiring  # noqa: F401
from .api import API
from .clock import SystemClock
from .contract_types import type_registry
from .operations import OperationsSettings
from .persistence import PostgresStore, State
from .retention import RetentionPolicy, maintain_postgres, pinned_references
from .runtime import Runtime
from .setup import Connection
from .work_capacity import effective_cpu_count
from .work_process import FRAME_LIMIT, exit_with_parent, read_frame, write_frame
from .work_routes import read_route

READ_OPERATIONS = frozenset(
    {
        "portable_export",
        "portable_preview",
        "title_preview",
        "catalog_preview",
        "catalog_care_preview",
        "catalog_scope_preview",
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

    def service_state(self) -> dict[str, Any]:
        return (
            self.service_snapshot if self.service_snapshot is not None else super().service_state()
        )

    async def submit(self, operation: str, payload: Any = None) -> Any:
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
    if not read_route(method, path):
        raise ValueError("work_route_invalid")
    database = Connection.model_validate(configuration["connection"])
    settings = OperationsSettings.model_validate(configuration["operations"])
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
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            tables = (
                {"registry_revision"} if method == "GET" and path.startswith("history/") else None
            )
            runtime.state = await store.load_read_state(connection, tables=tables)
            api = API(runtime, None, runtime.installation_id)
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
                        write_frame(sys.stdout.buffer, str(response.status).encode())
                        async for part in response.content.iter_chunked(FRAME_LIMIT):
                            write_frame(sys.stdout.buffer, part)
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
    # The socket is reachable only by this installation's process user.
    with tempfile.TemporaryDirectory(prefix="wuschel-worker-") as directory:
        asyncio.run(serve(configuration, Path(directory)))


if __name__ == "__main__":
    main()
