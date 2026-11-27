from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from core_contracts.clock import FakeClock
from core_contracts.history import HistoryQuery
from core_contracts.persistence import PostgresStore
from core_contracts.retention import EvidenceOutsideRetention
from core_contracts.retention_work import prepare_retention
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry

from .test_core import NOW
from .test_postgres import database as database
from .test_retention import prepared

ROOT = Path(__file__).resolve().parents[1]


async def test_postgres_retention_commit_rollback_and_restore(database, config, monkeypatch):
    store = PostgresStore(database, ROOT / "migrations", tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        refs = await prepared(runtime, config)
        writer = store.writer
        assert writer is not None

        class FailDelete:
            def __getattr__(self, name):
                return getattr(writer, name)

            async def execute(self, sql, *args):
                if sql.startswith("DELETE FROM source_observation_history"):
                    raise OSError("injected failure after rollup insert")
                return await writer.execute(sql, *args)

        with monkeypatch.context() as patch:
            patch.setattr(store, "_writer", lambda: FailDelete())
            with pytest.raises(OSError):
                await runtime.submit("retention_run")
        assert (await store.rollups(HistoryQuery()))["items"] == []
        assert await store.evidence(refs[0]) is not None
        await runtime.submit("retention_run")
        await runtime.submit("retention_run")
        assert (await store.rollups(HistoryQuery()))["items"][0]["average"] == 1.5
        with pytest.raises(EvidenceOutsideRetention):
            await store.evidence(refs[0])
        await runtime.stop()
        runtime = Runtime(store, FakeClock(NOW), type_registry())
        await runtime.start()
        assert await store.evidence(refs[-1]) is not None
        with pytest.raises(EvidenceOutsideRetention):
            await store.evidence(refs[0])
    finally:
        await runtime.stop()
        await store.close()


async def test_prepared_retention_publishes_once_even_after_a_lost_ack(database, config):
    store = PostgresStore(database, ROOT / "migrations", tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        refs = await prepared(runtime, config)
        reader = await asyncpg.connect(
            database, ssl=False, server_settings={"default_transaction_read_only": "on"}
        )
        try:
            async with reader.transaction(isolation="repeatable_read", readonly=True):
                batch = await prepare_retention(runtime, reader, uuid4())
        finally:
            await reader.close()
        assert await store.evidence(refs[0]) is not None
        assert (await store.rollups(HistoryQuery()))["items"] == []
        receipt = await runtime.submit("retention_run_prepared", batch)
        assert await runtime.submit("retention_run_prepared", batch) == receipt
        assert (
            sum(row["count"] for row in (await store.rollups(HistoryQuery()))["items"])
            == receipt["selected"]
        )
    finally:
        await runtime.stop()
        await store.close()
