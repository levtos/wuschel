"""Privacy-preserving timings for runtime queue operations.

DB timings count Store calls and their wall time, including encoding and waiting;
they are not individual SQL statements or server-side execution time.
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from time import perf_counter
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class Timing:
    task: asyncio.Task[Any] | None
    db_calls: int = 0
    db_ms: float = 0.0
    phases_ms: dict[str, float] = field(default_factory=dict)


current: ContextVar[Timing | None] = ContextVar("operation_timing", default=None)


def timed_phase[**P, T](fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(fn)
    async def measured(*args: P.args, **kwargs: P.kwargs) -> T:
        timing = current.get()
        start = perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            if timing is not None and timing.task is asyncio.current_task():
                timing.phases_ms[fn.__name__] = (
                    timing.phases_ms.get(fn.__name__, 0) + (perf_counter() - start) * 1000
                )

    return measured


def timed_sync[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def measured(*args: P.args, **kwargs: P.kwargs) -> T:
        timing = current.get()
        start = perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            if timing is not None and timing.task is asyncio.current_task():
                timing.phases_ms[fn.__name__] = (
                    timing.phases_ms.get(fn.__name__, 0) + (perf_counter() - start) * 1000
                )

    return measured


class TimedStore:
    """Delegate store attributes while measuring calls made by the queue task."""

    def __init__(self, wrapped: Any) -> None:
        object.__setattr__(self, "_wrapped", wrapped)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._wrapped, name, value)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._wrapped, name)
        if not inspect.iscoroutinefunction(value):
            return value

        async def measured(*args: Any, **kwargs: Any) -> Any:
            timing = current.get()
            if timing is None or timing.task is not asyncio.current_task():
                return await value(*args, **kwargs)
            start = perf_counter()
            timing.db_calls += 1
            try:
                return await value(*args, **kwargs)
            finally:
                timing.db_ms += (perf_counter() - start) * 1000

        return measured


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


class ObservationTimings:
    def __init__(self) -> None:
        self.started = perf_counter()
        self.wait: list[float] = []
        self.run: list[float] = []

    def add(self, wait: float, run: float) -> None:
        self.wait.append(wait)
        self.run.append(run)
        if len(self.wait) >= 4096 or perf_counter() - self.started >= 60:
            self.flush()

    def flush(self) -> None:
        if self.wait:
            LOGGER.info(
                "observation_timing",
                extra={
                    "count": len(self.wait),
                    "queue_wait_p50_ms": percentile(self.wait, 0.5),
                    "queue_wait_p95_ms": percentile(self.wait, 0.95),
                    "queue_wait_max_ms": max(self.wait),
                    "run_p50_ms": percentile(self.run, 0.5),
                    "run_p95_ms": percentile(self.run, 0.95),
                    "run_max_ms": max(self.run),
                },
            )
        self.wait.clear()
        self.run.clear()
        self.started = perf_counter()
