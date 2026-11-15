import json
import logging
import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core_contracts.api import API
from core_contracts.app import JsonFormatter, TextFormatter
from core_contracts.clock import FakeClock
from core_contracts.persistence import MemoryStore, PersistenceUnavailable
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
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
            "restore_clone",
            "restore_catalog_baseline",
            "restore_authoring",
            "restore_contexts",
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
            if (key.startswith("restore_") and key != "restore_migration") or key == "first_commit"
        )
        assert record.phases_ms["restore_migration"] >= sum(
            record.phases_ms[key]
            for key in (
                "restore_clone",
                "restore_catalog_baseline",
                "restore_authoring",
                "restore_contexts",
            )
        )
        assert runtime.service_state()["startup"] == {
            key: round(value) for key, value in record.phases_ms.items()
        }
        assert runtime.service_state()["startup_attempts"] == 1
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


async def test_ready_exposes_failed_start_and_retry_without_changing_readiness(
    aiohttp_client, tmp_path, monkeypatch
):
    runtime = Runtime(MemoryStore(), FakeClock(datetime(2026, 1, 1, tzinfo=UTC)), type_registry())
    client = await aiohttp_client(API(runtime, Tokens(tmp_path), str(uuid4())).application())
    original = runtime.store.load

    async def fail():
        raise PersistenceUnavailable("synthetic private connection detail")

    assert runtime.service_state()["startup_attempts"] == 0
    monkeypatch.setattr(runtime.store, "load", fail)
    with pytest.raises(PersistenceUnavailable):
        await runtime.start()
    response = await client.get("/health/ready")
    failed = await response.json()
    assert response.status == 503 and failed["startup_attempts"] == 1
    assert set(failed["startup"]) == {"store_open", "total"}
    assert "synthetic private" not in json.dumps(failed)
    monkeypatch.setattr(runtime.store, "load", original)
    # The app counts an attempt before connecting; start must not count it twice.
    started = runtime.begin_startup()
    try:
        await runtime.start(startup_started=started, store_open_ms=1.5)
        response = await client.get("/health/ready")
        result = await response.json()
        assert response.status == 503  # No active registry; timing never grants readiness.
        assert result["startup_attempts"] == 2
        assert result["startup"] == runtime.service_state()["startup"]
        assert result["startup"]["store_open"] == 2
        assert {
            "restore_clone",
            "restore_catalog_baseline",
            "restore_authoring",
            "restore_contexts",
        } <= result["startup"].keys()
        assert all(type(value) is int and value >= 0 for value in result["startup"].values())
    finally:
        await runtime.stop()
