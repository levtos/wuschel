"""Sequential evaluation in a private fork; one durable publication per block."""

import asyncio
import copy
import logging
from time import perf_counter

from .operation_timing import Timing, current
from .persistence import State
from .runtime import Runtime, Work

LOGGER = logging.getLogger(__name__)


class IngestQueue(asyncio.Queue[Work]):
    """A soft bound for coalescible state; protected events keep FIFO order."""

    ordered_put = False

    def full(self) -> bool:
        return False if self.ordered_put else super().full()

    def put_event(self, work: Work) -> None:
        self.ordered_put = True
        try:
            self.put_nowait(work)
        finally:
            self.ordered_put = False


class BlockRuntime(Runtime):
    async def _commit(
        self, state: State, *, publication: bool = False, resync: bool = False
    ) -> None:
        self.state = state
        if self.pending_latches is not None:
            self.latches = self.pending_latches
        self._block_refresh.update(self.pending_refresh)
        self._block_changed = True
        self._plan_evaluation(schedule=False)


async def process_block(runtime: Runtime, first: Work) -> None:
    worker = BlockRuntime.__new__(BlockRuntime)
    worker.__dict__ = runtime.__dict__.copy()
    worker.__dict__.pop("process", None)
    worker.state = runtime.state.live_clone()
    worker.latches = copy.deepcopy(runtime.latches)
    worker._observation_models = dict(runtime._observation_models)
    worker._field_models = dict(runtime._field_models)
    worker.pending_latches = None
    worker.pending_refresh = set()
    worker._block_refresh = set()
    worker._block_changed = False
    worker._block_active = True
    works: list[Work] = []
    work = first
    started = perf_counter()
    timing = Timing(asyncio.current_task())
    token = current.set(timing)
    deadline = started + 0.05
    error: Exception | None = None
    try:
        while True:
            works.append(work)
            await worker.process("observation", work.payload)
            if len(works) >= 100 or perf_counter() >= deadline:
                break
            if runtime.queue.empty() and first.future is not None:
                break
            try:
                work = await asyncio.wait_for(
                    runtime.queue.get(), max(0, deadline - perf_counter())
                )
            except TimeoutError:
                break
            if work.operation != "observation":
                runtime._next_work = work
                break
        if worker._block_changed:
            runtime.pending_latches = worker.latches
            runtime.pending_refresh = worker._block_refresh
            await runtime._commit(worker.state, publication=True)
            runtime._observation_models = worker._observation_models
            runtime._field_models = worker._field_models
        runtime.evaluation_stats = worker.evaluation_stats
    except asyncio.CancelledError:
        error = RuntimeError("observation_block_interrupted")
        runtime.pending_latches = None
        runtime.pending_refresh.clear()
        raise
    except Exception as caught:
        error = caught
        runtime.pending_latches = None
        runtime.pending_refresh.clear()
        if not runtime.persistence:
            for item in works:
                runtime._buffer_outage(item.payload)
        LOGGER.warning("observation_block_failed", extra={"error_type": type(caught).__name__})
    finally:
        if (
            runtime.overflow
            and runtime.queue.empty()
            and runtime._next_work is None
            and runtime.persistence
        ):
            try:
                await runtime._drain_overflow()
            except Exception as caught:
                error = error or caught
                LOGGER.warning("buffer_drain_failed", extra={"error_type": type(caught).__name__})
        finished = perf_counter()
        for item in works:
            if item.future is not None and not item.future.done():
                if error is None:
                    item.future.set_result(None)
                else:
                    item.future.set_exception(error)
            runtime.observation_timings.add(
                max(0, started - item.queued_at) * 1000, (finished - started) * 1000
            )
            runtime.queue.task_done()
        LOGGER.info(
            "observation_block", extra={"count": len(works), "run_ms": (finished - started) * 1000}
        )
        LOGGER.info(
            "operation_timing",
            extra={
                "operation": "observation_block",
                "count": len(works),
                "run_ms": (finished - started) * 1000,
                "db_calls": timing.db_calls,
                "db_ms": timing.db_ms,
            },
        )
        current.reset(token)
