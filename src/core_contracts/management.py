"""Prepare management requests on private snapshots; publish through the writer.

Published Runtime states are immutable. A snapshot pins their rows while the
worker owns every row it edits. Async database connections stay on their owning
loop; only immutable historical reads cross that boundary.
"""

import asyncio
import copy
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

from .operation_timing import Timing, current
from .persistence import ARCHIVE, PersistenceUnavailable, State, Store
from .runtime import Runtime

OPERATIONS = frozenset(
    {
        "validate",
        "draft_preview",
        "portable_preview",
        "portable_export",
        "draft_create",
        "draft_update",
        "activate",
        "rollback",
    }
)
CONTROL_TABLES = (
    "registry_draft",
    "registry_revision",
    "editorial_current",
    "editorial_draft",
    "catalog_head",
    "catalog_import",
    "catalog_request",
    "service_state",
)


class SnapshotStore:
    def __init__(self, store: Store, state: State, loop: asyncio.AbstractEventLoop) -> None:
        self.store, self.state, self.loop = store, state, loop

    def __getattr__(self, name: str) -> Any:
        async def read(*args: Any, **kwargs: Any) -> Any:
            if name in {"commit", "maintain_history", "close", "load"}:
                raise RuntimeError("management_read_only_store")
            if name == "get" and (args[0] not in ARCHIVE or args[1] in self.state.tables[args[0]]):
                return copy.deepcopy(self.state.tables[args[0]].get(args[1]))
            future = asyncio.run_coroutine_threadsafe(
                getattr(self.store, name)(*args, **kwargs), self.loop
            )
            return await asyncio.wrap_future(future)

        return read


@dataclass
class Preparation:
    base: State
    environment: dict[str, Any]
    operation: str = ""
    result: Any = None
    staged: State | None = None
    publication: bool = False
    pending_latches: Any = None
    pending_refresh: Any = None
    changes: Any = None


def capture(runtime: Runtime) -> Preparation:
    return Preparation(
        runtime.state,
        {
            name: copy.copy(getattr(runtime, name))
            for name in (
                "installation_id",
                "ingress_slug",
                "confirmed",
                "persistence",
                "first_snapshot",
                "bridge",
                "ha",
                "mqtt",
                "dependency_connections",
                "latches",
                "require_history_retention",
                "context_index",
            )
        },
    )


class Planner(Runtime):
    preparation: Preparation

    async def _commit(
        self, state: State, *, publication: bool = False, resync: bool = False
    ) -> None:
        if self.preparation.staged is not None:
            raise RuntimeError("management_multiple_commits")
        self.preparation.staged = state
        self.preparation.publication = publication
        self.preparation.pending_latches = self.pending_latches
        self.preparation.pending_refresh = self.pending_refresh
        self.state = state

    def _broadcast(self, event: dict[str, Any]) -> None:
        pass


def prepare(
    runtime: Runtime, plan: Preparation, payload: Any, loop: asyncio.AbstractEventLoop
) -> Preparation:
    worker = Planner(
        cast(Store, SnapshotStore(runtime.store, plan.base, loop)), runtime.clock, runtime.types
    )
    worker.preparation = plan
    worker.validation_cache = runtime.validation_cache
    worker.state = plan.base.clone()
    for name, value in plan.environment.items():
        setattr(worker, name, copy.deepcopy(value))

    async def run() -> Any:
        started = perf_counter()
        timing = Timing(asyncio.current_task())
        token = current.set(timing)
        try:
            return await worker.process(plan.operation, copy.deepcopy(payload))
        finally:
            logging.getLogger(__name__).info(
                "operation_timing",
                extra={
                    "operation": plan.operation,
                    "queue_wait_ms": 0,
                    "run_ms": (perf_counter() - started) * 1000,
                    "db_calls": timing.db_calls,
                    "db_ms": timing.db_ms,
                    "phases_ms": timing.phases_ms,
                },
            )
            current.reset(token)

    plan.result = asyncio.run(run())
    if plan.staged is not None:
        missing = object()
        plan.changes = {
            table: {
                key: rows.get(key)
                for key in rows.keys() | plan.base.tables[table].keys()
                if rows.get(key, missing) != plan.base.tables[table].get(key, missing)
            }
            for table, rows in plan.staged.tables.items()
        }
    return plan


async def execute(runtime: Runtime, operation: str, payload: Any) -> Any:
    async with runtime.management_slots:
        plan = await runtime.submit("management_snapshot")
        plan.operation = operation
        try:
            plan = await asyncio.to_thread(
                prepare, runtime, plan, payload, asyncio.get_running_loop()
            )
        except (PersistenceUnavailable, OSError) as error:
            runtime._unavailable()
            logging.getLogger(__name__).error(
                "management_unavailable", extra={"error_type": type(error).__name__}
            )
            raise
        return await runtime.submit("management_commit", plan)


async def commit(runtime: Runtime, plan: Preparation) -> Any:
    current, base = runtime.state, plan.base
    if not runtime.persistence:
        raise RuntimeError("persistence_unavailable")
    # Live observations may advance during draft editing. Control changes may
    # not: they can invalidate guards, import plans, history or validation.
    if current.active_revision != base.active_revision or any(
        current.tables[name] != base.tables[name]
        for name in CONTROL_TABLES
        if name != "service_state"
    ):
        raise ValueError("active_revision_conflict")
    if plan.staged is None:
        return plan.result
    activation = plan.operation in {"activate", "rollback"}
    if activation and (
        current.generation != base.generation
        or any(
            getattr(runtime, name) != plan.environment[name]
            for name in ("bridge", "ha", "mqtt", "dependency_connections")
        )
    ):
        raise ValueError("active_revision_conflict")
    merged = current.catalog_clone()
    for table, changes in plan.changes.items():
        for key, value in changes.items():
            if value is None:
                merged.tables[table].pop(key, None)
            else:
                merged.tables[table][key] = value
    if activation:
        merged.active_revision = plan.staged.active_revision
        merged.publication_seq = plan.staged.publication_seq
        merged.publications = plan.staged.publications
        runtime.pending_latches = plan.pending_latches
        runtime.pending_refresh = plan.pending_refresh
    old_config = runtime.config
    await runtime._commit(merged, publication=plan.publication)
    if activation:
        from .reconfiguration import same_ha_subscription

        if not same_ha_subscription(old_config, runtime.config):
            runtime.first_snapshot = False
        runtime.revision_changed.set()
        runtime.mqtt_revision_changed.set()
        runtime._broadcast(runtime.service_state())
    return plan.result
