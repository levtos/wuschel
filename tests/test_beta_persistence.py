"""B1-18: only the suite's isolated *_test PostgreSQL database is allowed."""

from datetime import timedelta

import pytest

from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.persistence import PostgresStore
from core_contracts.runtime import Runtime

from .conftest import activate
from .test_beta_openings import NOW, closed, configuration, field
from .test_postgres import MIGRATIONS
from .test_postgres import database as database

pytestmark = pytest.mark.postgres


async def test_B1_18_opening_proof_grace_and_zone_survive_postgres_reopen(database):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    rt = Runtime(store, FakeClock(NOW), type_registry(include_test_types=True))
    await rt.start()
    try:
        await activate(rt, configuration())
        await closed(rt)
        evidence = field(rt)["evidence"]
    finally:
        await rt.stop()
        await store.close()
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    rt = Runtime(
        store, FakeClock(NOW + timedelta(seconds=60)), type_registry(include_test_types=True)
    )
    await rt.start()
    try:
        assert field(rt)["value"] == "closed"
        assert field(rt)["evidence"] == evidence
        assert not rt.state.tables["node_state"]["test.opening0"]["temporal"]["grace_armed"]
        await closed(rt)
        rt.clock.advance(8401)
        await rt.submit("tick")
        deadline = field(rt)["held_until"]
    finally:
        await rt.stop()
        await store.close()
    await store.open()
    rt = Runtime(
        store, FakeClock(NOW + timedelta(seconds=8500)), type_registry(include_test_types=True)
    )
    await rt.start()
    try:
        assert field(rt)["status"] == "held"
        assert field(rt)["held_until"] == deadline
        assert field(rt, "test.zone.windows", "all_closed")["value"] is False
    finally:
        await rt.stop()
        await store.close()
