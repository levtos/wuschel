"""Coordinator entry point. Owns job scheduling, and no truth handlers."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
from aiohttp import web

from .model import canonical
from .operations import OperationsSettings
from .setup import Connection
from .work_capacity import WORKER_IDLE_S, WorkCapacity
from .work_journal import ARTIFACT_CHUNK, JobConflict, WorkJournal
from .work_process import WorkProcess, exit_with_parent, read_frame, write_frame
from .work_routes import is_long, read_route

WORKER_READY_TIMEOUT = 15
MAX_BODY = 50 * 1024 * 1024
TITLE_BODY = 102 * 1024 * 1024


@dataclass
class Worker:
    child: WorkProcess
    idle_at: float
    job: UUID | None = None
    long: bool = False
    task: asyncio.Task[None] | None = None
    pause: bool = False


class Coordinator:
    def __init__(
        self, journal: WorkJournal, configuration: dict[str, Any], capacity: WorkCapacity
    ) -> None:
        self.journal = journal
        self.configuration = configuration
        self.capacity = capacity
        self.workers: list[Worker] = []
        self.pressure = "normal"
        self.wake = asyncio.Event()
        self.loop = asyncio.get_running_loop()

    async def start_worker(self) -> Worker:
        child = await WorkProcess.start("core_contracts.work_worker", self.configuration)
        try:
            async with asyncio.timeout(WORKER_READY_TIMEOUT):
                if await child.receive() != b"ready":
                    raise ValueError("work_worker_invalid_startup")
        except BaseException:
            await child.stop()
            raise
        worker = Worker(child, self.loop.time())
        self.workers.append(worker)
        return worker

    async def drop_idle(self, worker: Worker) -> None:
        self.workers.remove(worker)
        await worker.child.stop()

    async def execute(self, worker: Worker, job_id: UUID, metadata: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(600 if worker.long else 90):
                await worker.child.send(canonical(metadata).encode())
                async for piece in self.journal.artifact(job_id, "request"):
                    await worker.child.send(piece)
                await worker.child.send(b"")
                status = int(await worker.child.receive())
                if not 200 <= status <= 599:
                    raise ValueError("work_response_invalid")
                part = 0
                while piece := await worker.child.receive():
                    await self.journal.append(job_id, "result", part, piece)
                    part += 1
            await self.journal.complete(
                job_id, "finished" if status < 400 else "failed", result_status=status
            )
        except asyncio.CancelledError:
            await worker.child.stop()
            if worker.pause:
                await self.journal.requeue(job_id)
            else:
                await self.journal.complete(job_id, "cancelled")
            raise
        except OSError, EOFError, ValueError, TimeoutError:
            await worker.child.stop()
            await self.journal.complete(job_id, "failed", error="work_worker_failed")
        finally:
            worker.job = None
            worker.task = None
            worker.idle_at = self.loop.time()
            self.wake.set()

    def set_pressure(self, state: str) -> None:
        if state != self.pressure:
            self.pressure = state
            self.wake.set()
        if state == "geschützt":
            for worker in self.workers:
                if worker.task and not worker.task.cancelling():
                    worker.pause = True
                    worker.task.cancel()

    async def schedule(self) -> None:
        prune_at = self.loop.time() + 60
        try:
            while True:
                await self.fill_slots()
                if self.loop.time() >= prune_at:
                    await self.journal.prune()
                    prune_at = self.loop.time() + 60
                try:
                    async with asyncio.timeout(1):
                        await self.wake.wait()
                except TimeoutError:
                    pass
                self.wake.clear()
        finally:
            async with asyncio.TaskGroup() as group:
                for worker in self.workers:
                    if worker.task:
                        worker.pause = True
                        worker.task.cancel()
                    else:
                        group.create_task(worker.child.stop())
            await asyncio.gather(
                *(worker.task for worker in self.workers if worker.task), return_exceptions=True
            )

    async def fill_slots(self) -> None:
        for worker in self.workers[:]:
            if worker.job is None and (
                worker.child.process.returncode is not None
                or (len(self.workers) > 1 and self.loop.time() - worker.idle_at >= WORKER_IDLE_S)
            ):
                await self.drop_idle(worker)
        if not self.workers:
            await self.start_worker()
        slots = self.capacity.slots(self.pressure)
        running = [worker for worker in self.workers if worker.job is not None]
        for _ in range(len(running), slots):
            if not await self.journal.waiting():
                return
            short_only = slots > 1 and sum(worker.long for worker in running) >= slots - 1
            record = await self.journal.claim(short_only=short_only)
            if not record:
                return
            available = next((item for item in self.workers if item.job is None), None)
            worker = available if available is not None else await self.start_worker()
            job_id: UUID = record["id"]
            metadata = json.loads(record["metadata"])
            worker.job = job_id
            worker.long = metadata["long"]
            worker.pause = False
            worker.task = asyncio.create_task(self.execute(worker, job_id, metadata))
            running.append(worker)

    async def status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "jobs": await self.journal.list(request.headers["X-Work-Owner"]),
                "workers": len(self.workers),
                "cpus": self.capacity.cpus,
                "needed": self.capacity.needed,
                "maximum": self.capacity.maximum,
            }
        )

    async def pressure_update(self, request: web.Request) -> web.Response:
        state = (await request.json())["state"]
        if state not in {"normal", "angespannt", "geschützt"}:
            raise web.HTTPBadRequest()
        self.set_pressure(state)
        return web.Response(status=204)

    async def admit(self, request: web.Request) -> web.Response:
        try:
            metadata = json.loads(request.headers["X-Work-Request"])
            method, path = metadata["method"], metadata["path"]
            if not read_route(method, path):
                raise web.HTTPNotFound()
            job_id = UUID(request.headers["X-Work-Id"])
            limit = TITLE_BODY if path == "title-import/preview" else MAX_BODY

            async def chunks() -> AsyncIterator[bytes]:
                size = 0
                async for piece in request.content.iter_chunked(ARTIFACT_CHUNK):
                    size += len(piece)
                    if size > limit:
                        raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=size)
                    yield piece

            metadata["long"] = is_long(method, path)
            job = await self.journal.admit(
                job_id, request.headers["X-Work-Owner"], path, metadata, chunks()
            )
        except JobConflict:
            return web.json_response({"error": "job_identity_conflict"}, status=409)
        except KeyError, ValueError:
            raise web.HTTPBadRequest() from None
        self.wake.set()
        return web.json_response(job, status=202)

    async def job(self, request: web.Request) -> web.StreamResponse:
        try:
            job_id = UUID(request.match_info["id"])
        except ValueError:
            raise web.HTTPNotFound() from None
        owner = request.headers["X-Work-Owner"]
        job = await self.journal.get(job_id, owner)
        if not job:
            raise web.HTTPNotFound()
        if request.method == "DELETE":
            if not await self.journal.cancel(job_id, owner):
                raise web.HTTPConflict()
            for worker in self.workers:
                if worker.job == job_id and worker.task and not worker.task.cancelling():
                    worker.task.cancel()
            self.wake.set()
            return web.Response(status=204)
        if request.path.endswith("/result"):
            if job["phase"] in {"waiting", "running"}:
                return web.json_response(job, status=202)
            if job["status"] is None:
                return web.json_response({"error": job["error"] or "work_cancelled"}, status=409)
            response = web.StreamResponse(
                status=job["status"], headers={"Content-Type": "application/json"}
            )
            await response.prepare(request)
            async for piece in self.journal.artifact(job_id, "result"):
                await response.write(piece)
            return response
        return web.json_response(job)

    def app(self) -> web.Application:
        app = web.Application(client_max_size=TITLE_BODY)
        app.add_routes(
            [
                web.get("/jobs", self.status),
                web.post("/jobs", self.admit),
                web.get("/jobs/{id}", self.job),
                web.get("/jobs/{id}/result", self.job),
                web.delete("/jobs/{id}", self.job),
                web.put("/pressure", self.pressure_update),
            ]
        )
        return app


async def run(configuration: dict[str, Any], socket: Path) -> None:
    settings = OperationsSettings.model_validate(configuration["operations"])
    capacity = WorkCapacity.detect(settings.work_max_workers)
    database = Connection.model_validate(configuration["connection"])
    connection = await asyncpg.connect(
        database.dsn(),
        ssl=database.tls(),
        timeout=settings.database_connect_timeout_s,
        command_timeout=settings.database_command_timeout_s,
        server_settings={"application_name": "wuschel_work_coordinator"},
    )
    try:
        journal = WorkJournal(connection)
        await journal.recover()
        coordinator = Coordinator(journal, configuration, capacity)
        await coordinator.start_worker()
        runner = web.AppRunner(coordinator.app(), access_log=None)
        await runner.setup()
        try:
            socket.unlink(missing_ok=True)
            await web.UnixSite(runner, str(socket)).start()
            write_frame(sys.stdout.buffer, b"ready")
            await coordinator.schedule()
        finally:
            await runner.cleanup()
    finally:
        await connection.close()


def main() -> None:
    exit_with_parent(int(sys.argv[1]))
    if os.name == "posix":
        os.umask(0o077)
    configuration = json.loads(read_frame(sys.stdin.buffer))
    asyncio.run(run(configuration, Path(sys.argv[2])))


if __name__ == "__main__":
    main()
