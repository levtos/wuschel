"""Process-local load state. Never writes truth or controls connections."""

from collections import deque
from typing import Any, Literal

from .operations import OperationsSettings

LoadState = Literal["normal", "angespannt", "geschützt"]
STATES: tuple[LoadState, ...] = ("normal", "angespannt", "geschützt")


class LoadGuard:
    def __init__(self, settings: OperationsSettings) -> None:
        self.settings = settings
        self.state: LoadState = "normal"
        self.candidate: LoadState = "normal"
        self.candidate_since = 0.0
        self.changed_at = 0.0
        self.metrics: dict[str, float | None] = {}
        self.reasons: list[str] = []
        self._overflows: deque[tuple[float, int]] = deque()
        self._overflow_total = 0

    def sample(
        self,
        now: float,
        *,
        lag_ms: float | None,
        queue_depth: int,
        queue_capacity: int,
        overflow_total: int,
        commit_p95_ms: float | None,
    ) -> bool:
        increase = max(0, overflow_total - self._overflow_total)
        self._overflow_total = overflow_total
        if increase:
            # At most one entry per second, including callers sampling more often.
            second = float(int(now))
            if self._overflows and self._overflows[-1][0] == second:
                _, count = self._overflows.pop()
                increase += count
            self._overflows.append((second, increase))
        while self._overflows and self._overflows[0][0] <= now - 60:
            self._overflows.popleft()
        self.metrics = {
            "lag_ms": lag_ms,
            "queue_percent": 100 * queue_depth / max(1, queue_capacity),
            "overflows_per_minute": float(sum(count for _, count in self._overflows)),
            "commit_p95_ms": commit_p95_ms,
        }
        levels = {
            key: 2
            if value >= getattr(self.settings, f"load_{key}_protected")
            else 1
            if value >= getattr(self.settings, f"load_{key}_strained")
            else 0
            for key, value in self.metrics.items()
            if value is not None
        }
        level = max(levels.values(), default=0)
        self.reasons = [key for key, severity in levels.items() if severity > 0]
        target = STATES[level]
        if target != self.candidate:
            self.candidate, self.candidate_since = target, now
        hold = (
            self.settings.load_enter_hold_s
            if level > STATES.index(self.state)
            else self.settings.load_recover_hold_s
        )
        if target == self.state or now - self.candidate_since < hold:
            return False
        self.state, self.changed_at = target, now
        return True

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "candidate": self.candidate,
            "changed_at_monotonic": self.changed_at,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }
