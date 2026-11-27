"""Monotonic HA input-to-acknowledgment measurements for published truth."""

from collections import deque


class TruthLatency:
    def __init__(self) -> None:
        self.pending: deque[tuple[int, float]] = deque()
        self.samples: deque[tuple[float, float]] = deque(maxlen=3600)

    def publish(self, sequence: int, queued_at: float) -> None:
        self.pending.append((sequence, queued_at))

    def acknowledge(self, sequence: int, now: float) -> None:
        while self.pending and self.pending[0][0] <= sequence:
            _, queued_at = self.pending.popleft()
            self.samples.append((now, max(0.0, (now - queued_at) * 1000)))

    def reset_pending(self) -> None:
        self.pending.clear()

    def oldest_ms(self, now: float) -> float | None:
        return max(0.0, (now - self.pending[0][1]) * 1000) if self.pending else None

    def p95_ms(self, now: float) -> float | None:
        while self.samples and self.samples[0][0] <= now - 60:
            self.samples.popleft()
        if len(self.samples) < 20:
            return None
        ordered = sorted(value for _, value in self.samples)
        return ordered[(95 * len(ordered) + 99) // 100 - 1]
