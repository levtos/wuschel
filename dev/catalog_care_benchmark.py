"""Run: uv run python -m dev.catalog_care_benchmark (synthetic MemoryStore only)."""

import asyncio
import json
from statistics import median
from time import perf_counter

from core_contracts import wiring  # noqa: F401
from core_contracts.catalog_care import all_rows, listing
from core_contracts.clock import SystemClock
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from tests.conftest import config as config_fixture
from tests.test_catalog_care import care, seeded
from tests.test_title_sync import observed


async def main():
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    try:
        catalog = await seeded(
            runtime,
            config_fixture.__wrapped__(),
            [observed(f"Synthetic title {index:05}") for index in range(5000)],
        )
        rows = all_rows(runtime.state, catalog)
        chosen = [next(iter(rows))]
        results = []
        for _ in range(3):
            body = care(runtime, chosen, "review")
            start = perf_counter()
            await runtime.submit("catalog_care_preview", {"request": body})
            preview = {**runtime.catalog_care_timings, "total_ms": (perf_counter() - start) * 1000}
            start = perf_counter()
            await runtime.submit("catalog_care_write", {"request": body})
            write = {**runtime.catalog_care_timings, "total_ms": (perf_counter() - start) * 1000}
            start = perf_counter()
            listing(runtime.state, catalog, view="all", sort="newest")
            reload_ms = (perf_counter() - start) * 1000
            results.append({"preview": preview, "write": write, "list_ms": reload_ms})
        print(
            json.dumps(
                {
                    "synthetic_titles": 5000,
                    "storage": "MemoryStore (no network)",
                    "samples": results,
                    "median_preview_ms": median(r["preview"]["total_ms"] for r in results),
                    "median_write_ms": median(r["write"]["total_ms"] for r in results),
                    "median_list_ms": median(r["list_ms"] for r in results),
                },
                indent=2,
            )
        )
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
