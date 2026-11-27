"""Resource limits for the work pool. No process or database side effects."""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

READ_CONNECTION_LIMIT = 6
WORK_CONNECTION_LIMIT = 1 + READ_CONNECTION_LIMIT
WORKER_IDLE_S = 90


def _cpu_quota(cgroup_root: Path) -> int | None:
    try:
        relative = next(
            line.removeprefix("0::")
            for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
            if line.startswith("0::")
        )
    except OSError, UnicodeError, StopIteration:
        return None
    scope = PurePosixPath(relative)
    if not scope.is_absolute() or ".." in scope.parts:
        return None
    quotas = []
    for path in (scope, *scope.parents):
        try:
            quota, period = (
                (cgroup_root.joinpath(*path.parts[1:]) / "cpu.max")
                .read_text(encoding="ascii")
                .split()
            )
            if quota != "max":
                quotas.append(max(1, int(quota) // int(period)))
        except OSError, UnicodeError, ValueError, ZeroDivisionError:
            continue
    return min(quotas, default=None)


def effective_cpu_count() -> int:
    visible = os.process_cpu_count() or 1
    quota = _cpu_quota(Path("/sys/fs/cgroup"))
    return min(visible, quota) if quota is not None else visible


@dataclass(frozen=True)
class WorkCapacity:
    cpus: int
    needed: int
    maximum: int

    @classmethod
    def detect(cls, maximum: int = 0) -> WorkCapacity:
        return cls.for_cpus(effective_cpu_count(), maximum)

    @classmethod
    def for_cpus(cls, cpus: int, maximum: int = 0) -> WorkCapacity:
        needed = cpus - 1
        default = max(1, 2 * needed)
        return cls(cpus, needed, max(min(2, cpus), maximum or default))

    @property
    def normal_slots(self) -> int:
        return min(self.maximum, READ_CONNECTION_LIMIT)

    def slots(self, state: str) -> int:
        if state == "geschützt":
            return 0
        if state == "angespannt":
            return min(self.normal_slots, max(1, self.needed // 2))
        return self.normal_slots
