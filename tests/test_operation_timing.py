import asyncio
import json
import logging

import pytest

from core_contracts.app import JsonFormatter
from core_contracts.clock import SystemClock
from core_contracts.operation_timing import ObservationTimings
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry


async def test_queue_timing_counts_store_calls_without_payload(caplog):
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())

    async def process(operation, payload):
        await runtime.store.get("registry_revision", "synthetic-private-key")
        return payload

    runtime.process = process
    runner = asyncio.create_task(runtime.run())
    with caplog.at_level(logging.INFO):
        assert await runtime.submit("synthetic_operation", "synthetic-private-value") == (
            "synthetic-private-value"
        )
        await runtime.queue.join()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    record = next(r for r in caplog.records if r.msg == "operation_timing")
    assert record.operation == "synthetic_operation"
    assert record.db_calls == 1
    assert record.queue_wait_ms >= 0
    assert record.run_ms >= record.db_ms >= 0
    assert "synthetic-private" not in str(record.__dict__)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "operation_timing"
    assert payload["db_calls"] == 1
    assert "synthetic-private" not in json.dumps(payload)


def test_observations_are_aggregated(caplog):
    timings = ObservationTimings()
    with caplog.at_level(logging.INFO):
        timings.add(1, 10)
        timings.add(2, 20)
        assert not caplog.records
        timings.flush()
    record = caplog.records[0]
    assert record.msg == "observation_timing"
    assert record.count == 2
    assert record.queue_wait_max_ms == 2
    assert record.run_max_ms == 20
