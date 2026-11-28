"""Main-process transport to the work coordinator.

This module streams administrative request and response bodies, and never runs
the jobs it transports.
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiohttp
import asyncpg
from aiohttp import web

from .clock import Clock
from .errors import AuthoringError
from .model import canonical
from .work_process import FRAME_LIMIT, WorkProcess
from .work_routes import work_route

LOGGER = logging.getLogger(__name__)


class WorkClient:
    def __init__(
        self,
        clock: Clock,
        discovery: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        apply: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        *,
        writer_idle: Callable[[], bool] | None = None,
    ) -> None:
        self.clock = clock
        self.discovery = discovery
        self.apply = apply
        self.writer_idle = writer_idle
        self.ready = asyncio.Event()
        self.session: aiohttp.ClientSession | None = None
        self.pressure = "normal"
        self.changed = asyncio.Event()
        self.retention_due = asyncio.Event()
        self.status_by_owner: dict[str, dict[str, Any]] = {}

    async def supervise(self, configuration: dict[str, Any]) -> None:
        with tempfile.TemporaryDirectory(prefix="wuschel-work-") as directory:
            socket = Path(directory) / "jobs.sock"
            metadata_socket = Path(directory) / "ha-metadata.sock"
            app = web.Application()
            app.router.add_get("/entities", self.entities)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            try:
                await web.UnixSite(runner, str(metadata_socket)).start()
                await self.run_coordinator(
                    {**configuration, "metadata_socket": str(metadata_socket)}, socket
                )
            finally:
                await runner.cleanup()

    async def entities(self, request: web.Request) -> web.Response:
        if self.discovery is None:
            raise web.HTTPServiceUnavailable()
        try:
            entities = await self.discovery()
        except aiohttp.ClientError, ConnectionError, TimeoutError, ValueError, TypeError:
            raise web.HTTPServiceUnavailable() from None
        return web.Response(
            text=await asyncio.to_thread(canonical, entities), content_type="application/json"
        )

    async def run_coordinator(self, configuration: dict[str, Any], socket: Path) -> None:
        delay = 0.25
        while True:
            child: WorkProcess | None = None
            try:
                child = await WorkProcess.start(
                    "core_contracts.work_coordinator", configuration, str(socket)
                )
                async with asyncio.timeout(30):
                    if await child.receive() != b"ready":
                        raise ValueError("work_coordinator_invalid_startup")
                async with aiohttp.ClientSession(
                    connector=aiohttp.UnixConnector(path=str(socket)),
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=2),
                ) as session:
                    self.session = session
                    async with (
                        asyncio.timeout(2),
                        session.put(
                            "http://localhost/pressure", json={"state": self.pressure}
                        ) as response,
                    ):
                        response.raise_for_status()
                    async with asyncio.TaskGroup() as group:
                        pressure = group.create_task(self.send_pressure())
                        status = group.create_task(self.refresh_status())
                        commits = group.create_task(self.publish_commits())
                        retention = group.create_task(self.queue_scheduled_retention())
                        self.ready.set()
                        delay = 0.25
                        await child.process.wait()
                        pressure.cancel()
                        status.cancel()
                        commits.cancel()
                        retention.cancel()
            except OSError, EOFError, ValueError, TimeoutError, aiohttp.ClientError:
                LOGGER.warning("work_coordinator_unavailable")
            finally:
                self.ready.clear()
                self.session = None
                self.status_by_owner = {}
                if child:
                    await child.stop()
            await self.clock.sleep(delay)
            delay = min(delay * 2, 10)

    def set_pressure(self, pressure: str) -> None:
        if pressure != self.pressure:
            self.pressure = pressure
            self.changed.set()

    async def send_pressure(self) -> None:
        while self.session:
            self.changed.clear()
            try:
                async with (
                    asyncio.timeout(2),
                    self.session.put(
                        "http://localhost/pressure", json={"state": self.pressure}
                    ) as response,
                ):
                    response.raise_for_status()
            except TimeoutError, aiohttp.ClientError:
                await self.clock.sleep(0.25)
                continue
            await self.changed.wait()

    async def queue_scheduled_retention(self) -> None:
        while self.session is not None:
            await self.retention_due.wait()
            owner = "internal:system"
            key = str(uuid4())
            metadata = canonical(
                {
                    "method": "POST",
                    "path": "history/retention/run",
                    "query": [],
                    "headers": {},
                    "actor": {"kind": "internal", "role": "admin"},
                    "service": None,
                }
            )
            while self.session is not None:
                try:
                    async with self.session.post(
                        "http://localhost/jobs",
                        headers={
                            "X-Work-Owner": owner,
                            "X-Work-Id": key,
                            "X-Work-Request": metadata,
                        },
                    ) as response:
                        response.raise_for_status()
                    self.retention_due.clear()
                    break
                except aiohttp.ClientError:
                    await self.clock.sleep(1)

    async def publish_commits(self) -> None:
        if self.apply is None:
            return
        while self.session is not None:
            try:
                if self.writer_idle is None or self.writer_idle():
                    await self.publish_next_commit(self.session, self.apply)
            except (
                aiohttp.ClientError,
                TimeoutError,
                OSError,
                RuntimeError,
                asyncpg.PostgresError,
                asyncpg.InterfaceError,
            ):
                # A lost acknowledgement is reconciled by the domain receipt
                # on the next claim, including after restart.
                await self.clock.sleep(0.5)
            await self.clock.sleep(0.1)

    async def publish_next_commit(
        self,
        session: aiohttp.ClientSession,
        apply: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        async with session.post("http://localhost/commits/claim") as response:
            if response.status == 204:
                return
            response.raise_for_status()
            job_id = str(UUID(response.headers["X-Work-Id"]))
            operation = response.headers["X-Work-Operation"]
            encoded = await response.read()
        status = 200
        replacement = b""
        try:
            plan = await asyncio.to_thread(json.loads, encoded)
            result = await apply(operation, plan)
            if (
                operation != "portable_stage_prepared"
                or result.get("draft_id") != plan["draft"]["draft_id"]
            ):
                replacement = (await asyncio.to_thread(canonical, result)).encode()
        except AuthoringError as error:
            status = 409
            replacement = canonical(
                {"error": error.code, "cause": error.code, "details": error.details}
            ).encode()
        except (ValueError, KeyError) as error:
            code = (
                str(error)
                if type(error) is ValueError
                and str(error)
                in {
                    "import_plan_conflict",
                    "retention_plan_conflict",
                    "draft_limit",
                    "active_revision_conflict",
                    "catalog_content_conflict",
                    "catalog_request_conflict",
                    "catalog_evaluation_failed",
                    "catalog_cleanup_conflict",
                    "catalog_migration_conflict",
                    "catalog_rollback_request_conflict",
                    "title_sync_plan_conflict",
                    "title_sync_mapping_conflict",
                }
                else "invalid_request"
            )
            status = 409 if "conflict" in code else 400
            replacement = canonical({"error": code}).encode()
        async with session.post(
            "http://localhost/commits/" + job_id,
            headers={"X-Work-Status": str(status)},
            data=replacement,
        ) as response:
            response.raise_for_status()

    async def connected(self) -> aiohttp.ClientSession:
        try:
            async with asyncio.timeout(5):
                await self.ready.wait()
        except TimeoutError:
            raise web.HTTPServiceUnavailable() from None
        if self.session is None:
            raise web.HTTPServiceUnavailable()
        return self.session

    @staticmethod
    def owner(request: web.Request) -> dict[str, str]:
        role = request["actor"]["role"]
        kind = request["actor"]["kind"]
        return {"X-Work-Owner": kind + ":" + role}

    async def queue(
        self,
        request: web.Request,
        path: str,
        *,
        service: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> web.StreamResponse | None:
        if not work_route(request.method, path):
            return None
        value = request.headers.get("Idempotency-Key")
        try:
            key = UUID(value) if value else uuid4()
        except ValueError:
            raise web.HTTPBadRequest() from None
        headers = self.owner(request)
        metadata = {
            "method": request.method,
            "path": path,
            "query": list(request.query.items()),
            "actor": request["actor"],
            "headers": {
                key: request.headers[key]
                for key in ("Content-Type", "X-Authoring-Protocol")
                if key in request.headers
            },
            "service": service,
        }
        headers["X-Work-Id"] = str(key)
        headers["X-Work-Request"] = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        # The transport metadata fits in a local HTTP header. The body is a stream.
        if len(headers["X-Work-Request"]) > 7500:
            raise web.HTTPBadRequest()
        try:
            session = await self.connected()
            async with session.post(
                "http://localhost/jobs",
                headers=headers,
                data=(
                    canonical(body).encode()
                    if body is not None
                    else request.content.iter_chunked(FRAME_LIMIT)
                ),
            ) as response:
                if response.status != 202:
                    return await self.forward(request, response)
                accepted = await response.json()
            location = f"work/jobs/{key}"
            if "respond-async" in {
                item.strip() for item in request.headers.get("Prefer", "").split(",")
            }:
                return web.json_response(
                    accepted,
                    status=202,
                    headers={"Location": location, "Preference-Applied": "respond-async"},
                )
            return await self.wait(request, location, headers)
        except aiohttp.ClientError:
            raise web.HTTPServiceUnavailable() from None

    async def wait(
        self, request: web.Request, path: str, headers: dict[str, str]
    ) -> web.StreamResponse:
        while True:
            try:
                session = await self.connected()
                async with session.get(
                    "http://localhost/" + path.removeprefix("work/") + "/result", headers=headers
                ) as response:
                    if response.status != 202:
                        return await self.forward(request, response)
            except aiohttp.ClientConnectionError:
                pass
            await self.clock.sleep(0.25)

    async def cancel_plan(self, request: web.Request, kind: str, key: str) -> None:
        session = await self.connected()
        try:
            async with session.delete(
                "http://localhost/plans",
                json={"kind": kind, "key": key, "actor": request["actor"]},
            ) as response:
                response.raise_for_status()
        except aiohttp.ClientError:
            raise web.HTTPServiceUnavailable() from None

    async def jobs(self, request: web.Request, path: str) -> web.StreamResponse:
        try:
            session = await self.connected()
            async with session.request(
                request.method,
                "http://localhost/" + path.removeprefix("work/"),
                headers=self.owner(request),
            ) as response:
                return await self.forward(request, response)
        except aiohttp.ClientError:
            raise web.HTTPServiceUnavailable() from None

    async def refresh_status(self) -> None:
        owners = ("ingress:admin", "token:admin")
        while self.session is not None:
            for owner in owners:
                try:
                    async with (
                        asyncio.timeout(1),
                        self.session.get(
                            "http://localhost/jobs", headers={"X-Work-Owner": owner}
                        ) as response,
                    ):
                        response.raise_for_status()
                        self.status_by_owner[owner] = {"available": True, **(await response.json())}
                except TimeoutError, aiohttp.ClientError:
                    self.status_by_owner.pop(owner, None)
            await self.clock.sleep(0.5)

    def current(self, request: web.Request) -> dict[str, Any]:
        return self.status_by_owner.get(
            self.owner(request)["X-Work-Owner"], {"available": False, "jobs": []}
        )

    @staticmethod
    async def forward(request: web.Request, response: aiohttp.ClientResponse) -> web.StreamResponse:
        if response.status == 204:
            return web.Response(status=204)
        result = web.StreamResponse(
            status=response.status,
            headers={
                "Content-Type": response.headers.get("Content-Type", "application/json"),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )
        await result.prepare(request)
        async for piece in response.content.iter_chunked(FRAME_LIMIT):
            await result.write(piece)
        return result
