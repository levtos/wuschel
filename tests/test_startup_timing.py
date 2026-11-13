import json
import logging
import math
from datetime import UTC, datetime
from uuid import uuid4

from core_contracts.app import JsonFormatter, TextFormatter
from core_contracts.clock import FakeClock
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry


async def test_memory_start_logs_exactly_one_private_numeric_timing(caplog):
    runtime = Runtime(MemoryStore(), FakeClock(datetime(2026, 1, 1, tzinfo=UTC)), type_registry())
    runtime.installation_id = str(uuid4())
    with caplog.at_level(logging.INFO, logger="core_contracts.runtime"):
        await runtime.start()
        await runtime.submit("tick")
    try:
        records = [record for record in caplog.records if record.msg == "startup_timing"]
        assert len(records) == 1
        record = records[0]
        assert set(record.phases_ms) == {
            "store_open",
            "load",
            "restore",
            "restore_catalog_heads",
            "restore_migration",
            "restore_observations",
            "restore_evaluation",
            "first_commit",
            "total",
        }
        assert all(math.isfinite(value) and value >= 0 for value in record.phases_ms.values())
        assert record.phases_ms["store_open"] == 0
        assert record.phases_ms["total"] >= record.phases_ms["restore"]
        assert record.phases_ms["restore"] >= sum(
            value
            for key, value in record.phases_ms.items()
            if key.startswith("restore_") or key == "first_commit"
        )
        assert json.loads(JsonFormatter().format(record)) == {
            "level": "INFO",
            "event": "startup_timing",
            "phases_ms": record.phases_ms,
        }
        text = TextFormatter().format(record)
        assert "startup_timing" in text and "phases_ms=" in text
        assert runtime.installation_id not in text and runtime.state.epoch_id not in text
    finally:
        await runtime.stop()
