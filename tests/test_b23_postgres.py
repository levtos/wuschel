"""The service-container release gate exercises the real transactional writer."""

import json
from contextvars import ContextVar
from statistics import fmean

import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.clock import SystemClock
from core_contracts.contract_types import type_registry
from core_contracts.history import HistoryQuery
from core_contracts.persistence import PostgresStore, State
from core_contracts.runtime import Runtime

from .test_b23_acceptance import genuine_load, ha_states, live_bridge, scale_runtime
from .test_postgres import MIGRATIONS
from .test_postgres import database as database

pytestmark = [pytest.mark.postgres, pytest.mark.timeout(180)]


async def test_postgres_live_size_twenty_per_second_with_genuine_management(
    database, aiohttp_server
):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    try:
        await genuine_load(aiohttp_server, store)
    finally:
        await store.close()


async def test_postgres_live_size_p7_commit_cost_and_no_commit_clone(
    database, aiohttp_server, monkeypatch
):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime, _ = await scale_runtime(store)
    original_commit, original_clone = store.commit, State.clone
    in_commit = ContextVar("postgres_commit_clone_guard", default=False)

    def clone(state):
        assert not in_commit.get(), "State.clone in PostgresStore.commit"
        return original_clone(state)

    async def commit(state):
        token = in_commit.set(True)
        try:
            return await original_commit(state)
        finally:
            in_commit.reset(token)

    monkeypatch.setattr(State, "clone", clone)
    monkeypatch.setattr(store, "commit", commit)
    try:
        async with live_bridge(runtime, aiohttp_server) as probe:
            for batch in range(100):
                for index in range(11):
                    await probe.emit(batch * 11 + index)
                await probe.wait()
            measured = [row for row in probe.commit_timings if 40 <= row["written_rows"] <= 100]
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "written_rows_all": sorted(
                            {row["written_rows"] for row in probe.commit_timings}
                        ),
                        "phase_means_all": {
                            key: fmean(row[key] for row in probe.commit_timings)
                            for key in ("commit_ms", "snapshot_ms", "changes_ms", "written_rows")
                        },
                    }
                )
            )
            assert len(measured) >= 20
            phases = {
                key: fmean(row[key] for row in measured)
                for key in ("commit_ms", "snapshot_ms", "changes_ms", "written_rows")
            }
            phases["non_sql_ms"] = phases["snapshot_ms"] + phases["changes_ms"]
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "p7_commit_mean": phases,
                        "blocks": len(measured),
                        "clone_calls": 0,
                    }
                )
            )
            assert phases["non_sql_ms"] < 15, phases
            probe.assert_transport()
    finally:
        await runtime.stop()
        await store.close()


async def test_postgres_writer_crash_mid_block_is_atomic_and_bridge_snapshot_recovers(
    database, aiohttp_server
):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    store.verify_changes = True
    await store.open()
    runtime, _ = await scale_runtime(store)
    installation = runtime.installation_id
    adapter = BridgeAdapter(runtime, None, "", "synthetic")
    binding = runtime.config.bindings[0]

    def observation(value):
        now = runtime.clock.now_utc().isoformat()
        return adapter.observation(
            binding,
            {
                "state": value,
                "attributes": {},
                "last_changed": now,
                "last_updated": now,
                "last_reported": now,
            },
            "live_change",
            {"time_fired": now},
            runtime.config,
        )

    reopened = None
    restarted = None
    try:
        block_commits = []
        store.before_commit = lambda: block_commits.append(True)
        for index in range(100):
            runtime.ingest(observation(bool(index % 2)))
        await runtime.queue.join()
        assert 1 <= len(block_commits) < 100
        # References use the adapter's fingerprint/version format; read them from
        # the already durable current evidence rather than guessing that format.
        baseline = await store.history(
            "source_observation_history",
            HistoryQuery(limit=100),
        )
        assert len(baseline["items"]) == 100
        expected = runtime.state.tables["source_observation_current"][binding.binding_id].copy()
        broken = []

        def crash():
            # Abruptly close the writer while SQL and metadata are still inside
            # its open transaction. PostgreSQL must roll the entire block back.
            assert store.writer is not None
            store.writer.terminate()
            broken.append(True)

        store.before_commit = crash
        open_block = [observation(bool(index % 2)) for index in range(5)]
        for item in open_block:
            runtime.ingest(item)
        await runtime.queue.join()
        assert broken == [True] and not runtime.persistence
        assert runtime.state.tables["source_observation_current"][binding.binding_id] == expected
        await runtime.stop()
        await store.close()
        reopened = PostgresStore(database, MIGRATIONS, tls=False)
        await reopened.open()
        durable = await reopened.load()
        assert durable.tables["source_observation_current"][binding.binding_id] == expected
        for item in open_block:
            assert await reopened.get("source_observation_history", item.reference) is None
        history = await reopened.history(
            "source_observation_history",
            HistoryQuery(limit=100),
        )
        assert history["items"] == baseline["items"]
        restarted = Runtime(reopened, SystemClock(), type_registry())
        restarted.installation_id = installation
        await restarted.start()
        states = ha_states(restarted)
        states["binary_sensor.synthetic_0"]["state"] = False
        async with live_bridge(restarted, aiohttp_server, states) as probe:
            assert len(probe.fake.projection_cache.rows) == 100
            assert restarted.first_snapshot
            assert (
                restarted.state.tables["source_observation_current"][binding.binding_id][
                    "value_raw"
                ]
                is False
            )
            assert all(
                row["fields"]["value"]["status"] == "valid"
                for row in restarted.state.tables["contract_state_current"].values()
            )
            probe.assert_transport()
    finally:
        store.before_commit = None
        await runtime.stop()
        await store.close()
        if restarted is not None:
            await restarted.stop()
        if reopened is not None:
            await reopened.close()
