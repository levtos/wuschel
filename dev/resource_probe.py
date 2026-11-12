"""Evidence-idle probe including age, edge, grace and report-heartbeat freshness."""

import asyncio
import copy
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from core_contracts import wiring  # noqa: F401
from core_contracts.clock import FakeClock
from core_contracts.evidence import Observation
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft
from core_contracts.testing.contract_types import type_registry


async def main():
    store = MemoryStore()
    runtime = Runtime(store, FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC)), type_registry())
    await runtime.start()
    registry = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/platform-alpha1/example-registry.json"
        ).read_text()
    )
    for operation in ("age", "edge", "grace"):
        fixture = copy.deepcopy(registry["contracts"][3])
        fixture["contract_id"] = f"fixture.{operation}"
        fixture["parameters"]["operation"] = operation
        registry["contracts"].append(fixture)
    registry["sources"][0]["freshness"] = {
        "mode": "report_heartbeat",
        "future_tolerance_s": 2,
        "interval_s": 3600,
    }
    draft = await runtime.submit("draft_create", {"config": registry})
    await activate_draft(runtime, draft, expected_active_revision=0)
    await runtime.submit(
        "observation",
        Observation(
            source_id="source.example",
            binding_id="binding.example",
            adapter="ha_state",
            value_raw=False,
            observation_kind="report",
            ha_last_reported=runtime.clock.now_utc(),
            ha_last_changed=runtime.clock.now_utc(),
            received_at=runtime.clock.now_utc(),
            epoch_id=runtime.state.epoch_id,
            ingest_seq=1,
        ),
    )
    # Settle the machine's declared deadline before measuring evidence-idle ticks.
    # The heartbeat remains fresh for the entire probe; expiry is tested separately.
    runtime.clock.advance(60)
    await runtime.submit("tick")
    baseline = (
        len(store.state.tables["contract_state_history"]),
        len(store.state.publications),
        runtime.state.generation,
        runtime.state.model_dump_json(),
    )
    samples, checkpoints = [], []
    for number in range(1, 1201):
        runtime.clock.advance(0.5)
        started = time.perf_counter()
        await runtime.submit("tick")
        elapsed = (time.perf_counter() - started) * 1000
        samples.append(elapsed)
        if number in (1, 100, 300, 600, 900, 1200):
            checkpoints.append(
                {
                    "ticks": number,
                    "sequence": runtime.state.publication_seq,
                    "history_rows": len(store.state.tables["contract_state_history"]),
                    "publications": len(store.state.publications),
                    "tick_ms": round(elapsed, 3),
                    "current_state_bytes": len(runtime.state.model_dump_json().encode()),
                }
            )
    assert (
        len(store.state.tables["contract_state_history"]),
        len(store.state.publications),
        runtime.state.generation,
        runtime.state.model_dump_json(),
    ) == baseline, "evidence-idle ticks must not commit, publish or grow history"
    print(
        json.dumps(
            {
                "fixtures": len(registry["contracts"]),
                "idle_history_growth": len(store.state.tables["contract_state_history"])
                - baseline[0],
                "idle_publication_growth": len(store.state.publications) - baseline[1],
                "checkpoints": checkpoints,
                "median_tick_ms": round(statistics.median(samples), 3),
                "p95_tick_ms": round(sorted(samples)[int(len(samples) * 0.95)], 3),
            },
            indent=2,
        )
    )
    await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
