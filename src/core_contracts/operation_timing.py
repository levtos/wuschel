"""Privacy-preserving timings for runtime queue operations.

DB timings count Store calls and their wall time, including encoding and waiting;
they are not individual SQL statements or server-side execution time.
"""

import asyncio
import inspect
import logging
import sys
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from heapq import nlargest
from itertools import chain
from threading import Event, Lock, Thread, get_ident
from time import perf_counter
from typing import Any, Literal

LOGGER = logging.getLogger(__name__)


@dataclass(eq=False)
class Activity:
    kind: Literal["operation", "route"]
    name: str
    started: float
    ended: float | None = None

    def log_fields(self, now: float) -> dict[str, str | float]:
        end = self.ended if self.ended is not None else now
        return {self.kind: self.name, "run_ms": round((end - self.started) * 1000, 3)}


class Activities:
    """Keep safe labels and timing for spans on the runtime's event loop."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self.monotonic = monotonic
        self.active: dict[Activity, None] = {}
        self.recent: deque[Activity] = deque(maxlen=128)
        self.last: dict[str, Activity] = {}

    @contextmanager
    def track(
        self, name: str, *, kind: Literal["operation", "route"] = "operation"
    ) -> Iterator[None]:
        activity = Activity(kind, name, self.monotonic())
        self.active[activity] = None
        try:
            yield
        finally:
            activity.ended = self.monotonic()
            del self.active[activity]
            self.recent.append(activity)
            self.last[kind] = activity

    def _latest(self, kind: Literal["operation", "route"]) -> Activity | None:
        return next(
            (item for item in reversed(self.active) if item.kind == kind), self.last.get(kind)
        )

    def operation(self) -> dict[str, str | float | bool | None]:
        activity = self._latest("operation")
        if activity is None:
            return {"operation": None, "run_ms": None, "operation_active": None}
        return {**activity.log_fields(self.monotonic()), "operation_active": activity.ended is None}

    def request(self) -> dict[str, str | float | bool]:
        activity = self._latest("route")
        if activity is None:
            return {}
        return {
            "route": activity.name,
            "request_run_ms": activity.log_fields(self.monotonic())["run_ms"],
            "request_active": activity.ended is None,
        }

    def overlapping(self, start: float, end: float) -> list[dict[str, str | float]]:
        # Include work that finished before the delayed sampler got CPU time.
        def overlap(item: Activity) -> float:
            completed = item.ended if item.ended is not None else end
            return min(completed, end) - max(item.started, start)

        candidates = (item for item in chain(self.active, self.recent) if overlap(item) > 0)
        return [item.log_fields(end) for item in nlargest(8, candidates, key=overlap)]


@dataclass
class Timing:
    task: asyncio.Task[Any] | None
    db_calls: int = 0
    db_ms: float = 0.0
    phases_ms: dict[str, float] = field(default_factory=dict)
    evaluated: int = 0

    def accounted_ms(self) -> float:
        return self.db_ms + sum(self.phases_ms.values())


current: ContextVar[Timing | None] = ContextVar("operation_timing", default=None)


@contextmanager
def phase(name: str) -> Iterator[None]:
    timing = current.get()
    if timing is None or timing.task is not asyncio.current_task():
        yield
        return
    accounted = timing.accounted_ms()
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - started) * 1000
        exclusive = max(0.0, elapsed - (timing.accounted_ms() - accounted))
        timing.phases_ms[name] = timing.phases_ms.get(name, 0.0) + exclusive


def timed_phase[**P, T](fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(fn)
    async def measured(*args: P.args, **kwargs: P.kwargs) -> T:
        with phase(fn.__name__):
            return await fn(*args, **kwargs)

    return measured


def timed_sync[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def measured(*args: P.args, **kwargs: P.kwargs) -> T:
        with phase(fn.__name__):
            return fn(*args, **kwargs)

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


class StallSampler:
    """Sample the loop thread while it is stalled. Emit only static frame names."""

    interval_s = 0.05
    threshold_s = 0.2

    def __init__(self) -> None:
        self.owner = get_ident()
        self.heartbeat = perf_counter()
        self.stop = Event()
        self.lock = Lock()
        self.count = 0
        self.peak_ms = 0.0
        self.stack: list[str] = []
        self.thread = Thread(target=self.run, name="wuschel-stall-sampler", daemon=True)

    def run(self) -> None:
        sampled = None
        while not self.stop.wait(self.interval_s):
            heartbeat = self.heartbeat
            elapsed = perf_counter() - heartbeat
            if elapsed <= self.threshold_s:
                continue
            with self.lock:
                if sampled != heartbeat:
                    sampled = heartbeat
                    self.count += 1
                    frame = sys._current_frames().get(self.owner)
                    stack: list[str] = []
                    while frame is not None and len(stack) < 16:
                        stack.append(
                            f"{frame.f_globals.get('__name__', '')}.{frame.f_code.co_name}:"
                            f"{frame.f_lineno}"
                        )
                        frame = frame.f_back
                    self.stack = stack
                self.peak_ms = max(self.peak_ms, elapsed * 1000)

    def take(self) -> dict[str, Any]:
        with self.lock:
            fields = {
                "sampled_stall_count": self.count,
                "sampled_stall_max_ms": round(self.peak_ms, 3),
                "sampled_stall_stack": self.stack,
            }
            self.count = 0
            self.peak_ms = 0.0
            self.stack = []
        return fields


class TruthTimings:
    """Bounded, value-free one-minute summaries of the truth path."""

    def __init__(self) -> None:
        self.started = perf_counter()
        self.samples: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=4096))
        self.block_total_ms = self.block_unassigned_ms = 0.0
        self.tick_runs = self.tick_evaluated = self.tick_evaluated_max = 0

    def record(self, operation: str, timing: Timing, elapsed: float) -> None:
        self.samples[operation].append(elapsed)
        for name, duration in timing.phases_ms.items():
            self.samples[name].append(duration)
        if timing.db_calls:
            self.samples["store"].append(timing.db_ms)
        if operation == "observation_block":
            self.block_total_ms += elapsed
            self.block_unassigned_ms += max(0.0, elapsed - timing.accounted_ms())
        if operation == "tick":
            self.tick_runs += 1
            self.tick_evaluated += timing.evaluated
            self.tick_evaluated_max = max(self.tick_evaluated_max, timing.evaluated)

    def report(self, sampler: StallSampler) -> None:
        now = perf_counter()
        if now - self.started < 60:
            return
        stall = sampler.take()
        if self.samples or stall["sampled_stall_count"]:
            LOGGER.info(
                "truth_path_timing",
                extra={
                    "phase_p95_ms": {
                        key: round(percentile(list(values), 0.95), 3)
                        for key, values in sorted(self.samples.items())
                    },
                    "unassigned_block_percent": round(
                        100 * self.block_unassigned_ms / self.block_total_ms, 2
                    )
                    if self.block_total_ms
                    else None,
                    "tick_runs": self.tick_runs,
                    "tick_evaluated": self.tick_evaluated,
                    "tick_evaluated_max": self.tick_evaluated_max,
                    **stall,
                },
            )
        self.started = now
        self.samples.clear()
        self.block_total_ms = self.block_unassigned_ms = 0.0
        self.tick_runs = self.tick_evaluated = self.tick_evaluated_max = 0


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
        if self.wait and LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
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
