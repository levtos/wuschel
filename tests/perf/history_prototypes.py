"""Compare history algorithms on synthetic, identical source revisions.

Run with: uv run python -m tests.perf.history_prototypes
This is a narrow experiment, not a replacement validator.
"""

import asyncio
import copy
import json
from statistics import median
from time import perf_counter

from core_contracts.clock import SystemClock
from core_contracts.persistence import MemoryStore, State
from core_contracts.registry import RegistryConfig, storage_document
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from tests.perf.benchmark import cached_history, synthetic_config


async def main():
    document = storage_document(RegistryConfig.model_validate(synthetic_config()))
    state = State()
    state.active_revision = 11
    state.tables["registry_revision"] = {
        str(i): {"revision": i, "config": document, "checksum": str(i)} for i in range(1, 12)
    }
    store = MemoryStore()
    store.state = copy.deepcopy(state)
    runtime = Runtime(store, SystemClock(), type_registry())
    runtime.state = state
    candidate = RegistryConfig.model_validate(document)
    out = {}
    for name, work in (
        ("baseline", lambda: runtime._source_types(candidate)),
        ("cache", lambda: cached_history(runtime, candidate, skip_unchanged=False)),
        ("skip_identical", lambda: cached_history(runtime, candidate, skip_unchanged=True)),
    ):
        times = []
        for _ in range(3):
            start = perf_counter()
            await work()
            times.append((perf_counter() - start) * 1000)
        out[name] = {"samples_ms": times, "median_ms": median(times)}
    print(json.dumps({"synthetic_revisions": 11, "results": out}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
