import json
from time import perf_counter

from core_contracts.adapters.ha import BridgeAdapter

from .test_b23_acceptance import ha_states, scale_runtime


async def test_live_size_sustains_at_least_one_hundred_evaluations_per_second():
    runtime, _ = await scale_runtime()
    try:
        adapter = BridgeAdapter(runtime, None, "", "synthetic")
        await runtime.submit("bridge_state", ("connected", "connected"))
        await adapter.event({"kind": "snapshot", "states": ha_states(runtime)}, runtime.config)
        await runtime.queue.join()
        before = runtime.state.generation
        started = perf_counter()
        count = 1000
        binding = runtime.config.bindings[0]
        for index in range(count):
            now = runtime.clock.now_utc().isoformat()
            observation = adapter.observation(
                binding,
                {
                    "state": bool(index % 2),
                    "attributes": {},
                    "last_updated": now,
                    "last_changed": now,
                    "last_reported": now,
                },
                "live_change",
                {"time_fired": now},
                runtime.config,
            )
            runtime.ingest(observation)
        await runtime.queue.join()
        elapsed = perf_counter() - started
        rate = count / elapsed
        history = await runtime.store.rows("source_observation_history", limit=2000)
        assert len([row for row in history if row["observation_kind"] == "live_change"]) == count
        assert rate >= 100, {"evaluations_per_s": rate, "seconds": elapsed}
        assert runtime.state.generation - before < count // 2
        assert not runtime.latest and not runtime.overflow and not runtime.resubscribe.is_set()
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "evaluations": count,
                    "seconds": elapsed,
                    "evaluations_per_s": rate,
                    "commits": runtime.state.generation - before,
                }
            )
        )
    finally:
        await runtime.stop()
