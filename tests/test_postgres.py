import copy
import os
import shutil
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from core_contracts.clock import FakeClock
from core_contracts.persistence import PersistenceUnavailable, PostgresStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from tests.conftest import activate_draft

from .conftest import activate
from .test_core import NOW
from .test_phase1a import exercise_chain, field, observation
from .test_runtime import obs

pytestmark = pytest.mark.postgres
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


async def test_b20_cleanup_reset_is_atomic_and_idempotent_after_reopen(database, config):
    from .test_catalog_cleanup import application, initial, populated

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        catalog = await populated(runtime, config)
        plan = await runtime.submit(
            "catalog_cleanup_preview",
            initial(catalog, select_filtered=True, filters={"kind": "unclear"}),
        )
        body = application(plan)
        before = copy.deepcopy(runtime.state.tables["catalog_sighting"])

        def fail():
            raise RuntimeError("synthetic cleanup transaction failure")

        store.before_commit = fail
        with pytest.raises(RuntimeError, match="synthetic cleanup transaction failure"):
            await runtime.submit("catalog_cleanup_apply", body)
        saved = await store.load()
        assert saved.tables["catalog_sighting"] == before
        assert "catalog_counter_epoch" not in saved.tables["service_state"]
        assert not await store.get("catalog_request", body["request_id"])
        store.before_commit = None
        await runtime.submit("recover", {})
        result = await runtime.submit("catalog_cleanup_apply", body)
        assert result["removed"] == 3
        saved = await store.load()
        assert saved.tables["catalog_sighting"] == {}
        assert (
            saved.tables["service_state"]["catalog_counter_epoch"]["request_id"]
            == body["request_id"]
        )
        runtime.state = saved
        runtime.catalog_cleanup_plans.clear()
        assert await runtime.submit("catalog_cleanup_apply", body) == result
    finally:
        store.before_commit = None
        await runtime.stop()
        await store.close()


async def test_alpha10_catalog_and_import_survive_reopen_with_atomic_history(database, config):
    from .test_managed_catalog import change, configured, request
    from .test_portable import export, plan, stage

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await activate(runtime, configured(config))
        body = request(runtime)
        saved = await runtime.submit("catalog_write", {"request": body})
        # Dedicated history readers must exceed both generic limits (100/1000).
        for start in range(0, 1100, 100):
            await runtime.submit(
                "catalog_write",
                {
                    "request": request(
                        runtime,
                        [change(f"Synthetic {i}") for i in range(start, start + 100)],
                        start // 100 + 1,
                    )
                },
            )
        package = await export(runtime)
        preview = await plan(runtime, package)
        imported = await stage(runtime, package, preview)
        await activate_draft(runtime, imported, expected_active_revision=1)
    finally:
        await runtime.stop()
        await store.close()
    reopened = PostgresStore(database, MIGRATIONS, tls=False)
    await reopened.open()
    restarted = Runtime(reopened, FakeClock(NOW), type_registry())
    await restarted.start()
    try:
        assert await restarted.submit("catalog_write", {"request": body}) == saved
        catalog_key = next(iter(restarted.state.tables["catalog_head"]))
        assert len(await reopened.catalog_revisions(catalog_key, 13)) == 12
        assert [row["revision"] for row in await reopened.catalog_revisions(catalog_key, 7)] == [
            6,
            5,
            4,
            3,
            2,
            1,
        ]
        assert len(await reopened.managed_entries(catalog_key, 12)) == 1101
        historical = await restarted.submit(
            "catalog_revision",
            {"catalog_id": "example.titles", "definition_version": 1, "revision": 12},
        )
        assert historical["total"] == 1101
        assert (await export(restarted))["catalog_contents"] == package["catalog_contents"]
    finally:
        await restarted.stop()
        await reopened.close()


async def test_alpha9_editorial_reopen_and_rollback_keep_current_room(database, config):
    from .test_alpha9_editorial import request, save

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        first = await activate(runtime, config)
        saved = await save(runtime, request())
        changed = copy.deepcopy(config)
        changed["contracts"][0]["display_name"] = "Neuer Name"
        await activate(runtime, changed)
    finally:
        await runtime.stop()
        await store.close()
    reopened = PostgresStore(database, MIGRATIONS, tls=False)
    await reopened.open()
    restarted = Runtime(reopened, FakeClock(NOW), type_registry())
    await restarted.start()
    try:
        await restarted.submit(
            "rollback",
            {
                "revision": first["revision"],
                "expected_active_revision": restarted.state.active_revision,
            },
        )
        assert restarted.state.tables["editorial_current"]["contract:fixture.echo"] == saved
        assert await reopened.get("editorial_current", "contract:fixture.echo") == saved
    finally:
        await restarted.stop()
        await reopened.close()


async def test_alpha9_manual_result_survives_reopen_without_renewing_validity(database, config):
    from .test_alpha9_manual_source import body, enter, manual_config, value

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await activate(runtime, manual_config(config, ttl=60))
        request = body(runtime)
        result = await enter(runtime, request)
        observation = copy.deepcopy(runtime.state.tables["source_observation_current"]["binding.a"])
    finally:
        await runtime.stop()
        await store.close()
    reopened = PostgresStore(database, MIGRATIONS, tls=False)
    await reopened.open()
    restarted = Runtime(reopened, FakeClock(NOW + timedelta(seconds=30)), type_registry())
    await restarted.start()
    try:
        assert await enter(restarted, request) == result
        assert restarted.state.tables["source_observation_current"]["binding.a"] == observation
        assert value(restarted)["status"] == "valid"
    finally:
        await restarted.stop()
        await reopened.close()


async def test_alpha7_manual_restart_survives_process_restart_without_replay(database, config):
    from .test_alpha7_manual_restart import machine, request_for, restart, setup_machine
    from .test_declarative import observation as declarative_observation

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await setup_machine(runtime, config, recovery=True)
        body = await request_for(runtime)
        result = await restart(runtime, body)
        assert result["status"] == "accepted"
    finally:
        await runtime.stop()
        await store.close()
    reopened = PostgresStore(database, MIGRATIONS, tls=False)
    await reopened.open()
    restarted = Runtime(reopened, FakeClock(NOW), type_registry())
    await restarted.start()
    try:
        assert machine(restarted)["episode_id"] == result["episode_id"]
        assert machine(restarted)["manual_restart"]
        assert await restart(restarted, body) == result
        assert (
            len(
                [
                    row
                    for row in await reopened.rows("diagnostic_event")
                    if row.get("operation") == "manual_restart"
                ]
            )
            == 1
        )
        restarted.clock.advance(1)
        await restarted.submit("observation", declarative_observation(restarted, True))
        assert not machine(restarted).get("manual_restart", False)
        assert machine(restarted)["origin"] == "automatic"
    finally:
        await restarted.stop()
        await reopened.close()


async def test_alpha6_source_lock_survives_removal_and_process_restart(database, config):
    import copy

    from core_contracts.validation import RegistryValidationError

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await activate(runtime, config)
        await activate(runtime, {**config, "sources": [], "bindings": [], "contracts": []})
    finally:
        await runtime.stop()
        await store.close()
    reopened = PostgresStore(database, MIGRATIONS, tls=False)
    await reopened.open()
    restarted = Runtime(reopened, FakeClock(NOW), type_registry())
    await restarted.start()
    try:
        history = await reopened.source_history("source.a")
        assert len(history) == 1 and history[0]["source_type"] == "boolean"
        changed = copy.deepcopy(config)
        changed["sources"][0]["value_schema"] = {
            "type": "bool",
            "unit": "changed",
            "required": True,
            "physical_state": False,
        }
        with pytest.raises(RegistryValidationError) as error:
            await restarted.submit("validate", changed)
        assert error.value.issues[0].code == "immutable_source_type"
        assert restarted.state.active_revision == 2
    finally:
        await restarted.stop()
        await reopened.close()


async def test_delta2_guard_error_persistent_http_replay_after_restart(
    database, config, aiohttp_client, tmp_path, monkeypatch
):
    from .test_alpha4_delta2 import assert_replay, exercise_guard_failure
    from .test_workbench_api import api_client

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        command, expected = await exercise_guard_failure(
            runtime, config, aiohttp_client, tmp_path, monkeypatch
        )
    finally:
        await runtime.stop()
        await store.close()
    # Reopen the database with a fresh Store and Runtime; no in-memory command cache.
    restored = PostgresStore(database, MIGRATIONS, tls=False)
    await restored.open()
    restarted = Runtime(restored, FakeClock(NOW), type_registry())
    await restarted.start()
    try:
        _, client, headers = await api_client(restarted, aiohttp_client, tmp_path)
        await assert_replay(restarted, client, headers, command, expected)
        assert (await restored.get("command_log", command["command_id"]))["result"] == expected
    finally:
        await restarted.stop()
        await restored.close()


async def test_review_hysteresis_recovery_and_time_filters(database, config):
    from .test_alpha4_review import exercise_latch_restore, observation

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        end = await exercise_latch_restore(runtime, config)

        def fail():
            raise OSError("injected transaction failure")

        store.before_commit = fail
        runtime.clock.advance(1)
        with pytest.raises(OSError):
            await runtime.submit("observation", observation(runtime, 3, ingest_seq=5))
        assert runtime.latches["fixture.echo:latch"].latch is True
        store.before_commit = None
        await runtime.submit("recover")
        await runtime.submit(
            "snapshot",
            [
                observation(runtime, 3, ingest_seq=6).model_copy(
                    update={"observation_kind": "snapshot"}
                )
            ],
        )
        assert field(runtime)["value"] is False
        assert field(runtime, key="held")["held_until"] == end
        runtime.clock.advance(1)
        await runtime.submit("observation", observation(runtime, 6, ingest_seq=7))
        runtime.clock.advance(1)
        await runtime.submit("observation", observation(runtime, 3, ingest_seq=8))
        assert field(runtime)["value"] is True
    finally:
        await runtime.stop()
        await store.close()
    restored = PostgresStore(database, MIGRATIONS, tls=False)
    await restored.open()
    restarted = Runtime(restored, runtime.clock, type_registry())
    await restarted.start()
    try:
        await restarted.submit(
            "snapshot",
            [
                observation(restarted, 3, ingest_seq=9).model_copy(
                    update={"observation_kind": "snapshot"}
                )
            ],
        )
        assert field(restarted)["value"] is False
        assert field(restarted, key="held")["held_until"] == end
    finally:
        await restarted.stop()
        await restored.close()


async def test_review_history_time_filters(database):
    from .test_alpha4_review import exercise_history_times

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    try:
        await exercise_history_times(store)
    finally:
        await store.close()


async def test_multistage_chain_postgres_restart_restore(database, config):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    end = await exercise_chain(runtime, config)
    sequence = runtime.state.publication_seq
    await runtime.stop()
    await store.close()
    restored_store = PostgresStore(database, MIGRATIONS, tls=False)
    await restored_store.open()
    restarted = Runtime(restored_store, runtime.clock, type_registry())
    await restarted.start()
    try:
        assert restarted.state.publication_seq > sequence
        assert field(restarted, "grace")["held_until"] == end
        assert field(restarted, "not")["status"] == "held"
        restarted.clock.advance(11)
        await restarted.submit("tick")
        assert field(restarted, "stable")["status"] == "unknown"
        await restarted.submit("observation", observation(restarted, True, "b"))
        restarted.clock.advance(2)
        await restarted.submit("tick")
        assert field(restarted, "stable")["quality"] == "degraded"
        await restarted.submit("rollback", dict(revision=1, expected_active_revision=1))
        assert restarted.state.active_revision == 2
        assert len((await restored_store.load()).tables["contract_state_current"]) == 4
    finally:
        await restarted.stop()
        await restored_store.close()


async def test_declarative_cascade_history_immutability_and_recovery(database, config):
    import copy

    from core_contracts.history import HistoryQuery
    from core_contracts.validation import RegistryValidationError

    from .test_alpha4_acceptance import exercise_cascade

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    grace, deadline = await exercise_cascade(runtime, config)
    first = await store.history("source_observation_history", HistoryQuery(limit=2))
    assert len(first["items"]) == 2 and first["next_cursor"]
    following = await store.history(
        "source_observation_history", HistoryQuery(limit=2, cursor=first["next_cursor"])
    )
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in following["items"]}
    for reference in field(runtime, "timed")["evidence"]:
        assert await store.evidence(reference) is not None
    assert await store.definition("test.timed", 1) is not None
    assert (await store.history("sm_episode", HistoryQuery(contract_id="fixture.flow")))["items"]
    assert (await store.history("deadline", HistoryQuery(contract_id="fixture.flow")))["items"]
    for table in ("sm_episode", "deadline"):
        await store.writer.execute(f"UPDATE {table} SET payload=payload-'contract_id'")
        assert (await store.history(table, HistoryQuery(contract_id="fixture.flow")))["items"]
        assert not (await store.history(table, HistoryQuery(contract_id="fixture.other")))["items"]
    committed = runtime.snapshot()
    trace = copy.deepcopy(runtime.state.tables["node_state"]["fixture.flow"]["trace"])

    def fail():
        raise OSError("test transaction rollback")

    store.before_commit = fail
    runtime.clock.advance(1)
    with pytest.raises(OSError):
        await runtime.submit(
            "observation", observation(runtime, True).model_copy(update={"ingest_seq": 5})
        )
    assert runtime.state.publication_seq == committed["publication_seq"]
    assert runtime.state.tables["node_state"]["fixture.flow"]["trace"] == trace
    store.before_commit = None
    await runtime.submit("recover")
    assert field(runtime, "timed")["held_until"] == grace
    await runtime.stop()
    await store.close()
    restored = PostgresStore(database, MIGRATIONS, tls=False)
    await restored.open()
    restarted = Runtime(restored, runtime.clock, type_registry())
    await restarted.start()
    try:
        assert field(restarted, "timed")["held_until"] == grace
        assert restarted.state.tables["sm_instance"]["fixture.flow"]["deadline"] == deadline
        changed = copy.deepcopy(config)
        next(d for d in changed["definitions"] if d["type_id"] == "test.timed")["rules"]["grace"][
            "duration_s"
        ] = 5
        with pytest.raises(RegistryValidationError):
            await restarted.submit("validate", changed)
        await restarted.submit("rollback", {"revision": 1, "expected_active_revision": 1})
        assert restarted.state.active_revision == 2
        assert (await restored.history("diagnostic_event", HistoryQuery()))["items"]
        command = (await restored.history("command_log", HistoryQuery()))["items"][0]
        sequence = restarted.state.publication_seq
        assert await restarted.submit("command", command["command"]) == command["result"]
        assert restarted.state.publication_seq == sequence
    finally:
        await restarted.stop()
        await restored.close()


@pytest_asyncio.fixture
async def database():
    dsn = os.environ.get("TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("environment: TEST_DATABASE_DSN / PostgreSQL unavailable")
    parsed = urlsplit(dsn)
    if not parsed.path.endswith("_test"):
        pytest.fail("TEST_DATABASE_DSN must name a dedicated *_test database")
    admin = await asyncpg.connect(dsn, ssl=False)
    name = "cc_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    isolated = urlunsplit(parsed._replace(path="/" + name))
    try:
        yield isolated
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1", name
        )
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def test_alpha3_fingerprint_upgrade_restart_and_rollback(database, config):
    import copy

    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    await activate(runtime, config)
    original = runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"]
    await runtime.stop()
    await store.close()
    for upgrade in (False, True):
        store = PostgresStore(database, MIGRATIONS, tls=False)
        await store.open()
        runtime = Runtime(store, FakeClock(NOW), type_registry())
        await runtime.start()
        try:
            if not upgrade:
                assert runtime.config.fingerprint_version == 1
                assert runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"] == original
                changed = copy.deepcopy(config)
                changed["fingerprint_version"] = 2
                assert (await runtime.submit("validate", changed))["diff"]["fingerprint_version"][
                    "context_migration"
                ]
                await activate(runtime, changed)
            else:
                assert runtime.config.fingerprint_version == 2
                assert runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"] != original
                await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 2})
                assert runtime.config.fingerprint_version == 1
                assert runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"] == original
        finally:
            await runtime.stop()
            await store.close()


async def test_postgres_restart_commit_failure_and_writer_lock(database, config):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    contender = PostgresStore(database, MIGRATIONS, tls=False)
    with pytest.raises(PersistenceUnavailable, match="writer_lock_unavailable"):
        await contender.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    seq = runtime.state.publication_seq

    def crash():
        raise OSError("before commit")

    store.before_commit = crash
    with pytest.raises(OSError):
        await runtime.submit("observation", obs(runtime, False))
    assert runtime.state.publication_seq == seq
    store.before_commit = None
    runtime.task.cancel()
    import asyncio

    await asyncio.gather(runtime.task, return_exceptions=True)
    await store.close()
    await contender.open()
    restarted = Runtime(contender, runtime.clock, type_registry())
    await restarted.start()
    assert restarted.state.active_revision == 1
    assert (
        restarted.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"] == "unknown"
    )
    assert restarted.state.publication_seq == seq + 1
    await restarted.stop()
    await contender.close()


async def test_migration_checksum_and_newer_schema(database, tmp_path):
    migrations = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, migrations)
    store = PostgresStore(database, migrations, tls=False)
    await store.open()
    await store.close()
    path = migrations / "001_foundation.sql"
    original = path.read_text()
    path.write_text(original + "\n-- changed\n")
    with pytest.raises(ValueError, match="checksum"):
        await store.open()
    path.write_text(original)
    await store.open()
    await store.writer.execute("INSERT INTO cc_schema_migrations VALUES (99,'future')")
    await store.close()
    with pytest.raises(ValueError, match="newer_database_schema"):
        await store.open()


async def test_lock_loss_blocks_commit(database):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    state = await store.load()
    await store.writer.execute("SELECT pg_advisory_unlock_all()")
    with pytest.raises(PersistenceUnavailable, match="writer_lock_unavailable"):
        await store.commit(state)
    await store.close()


async def test_connection_loss_recovery(database, config):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    await activate(runtime, config)
    epoch = runtime.state.epoch_id
    store.writer.terminate()
    with pytest.raises(PersistenceUnavailable):
        await runtime.submit("tick")
    assert not runtime.confirmed
    await store.close()
    await store.open()
    await runtime.submit("recover")
    assert runtime.state.epoch_id != epoch
    await runtime.stop()
    await store.close()


async def test_history_is_durable_bounded_ordered_and_not_loaded(database, config):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await activate(runtime, config)
        for number in range(110):
            runtime.clock.advance(1)
            await runtime.submit("observation", obs(runtime, number))
        loaded = await store.load()
        assert not loaded.tables["contract_state_history"] and not loaded.publications
        rows = await store.rows("contract_state_history", contract_id="fixture.echo")
        assert len(rows) == 100
        assert rows[0]["fields"]["value"]["value"] == 109
        assert [row["publication_seq"] for row in rows] == sorted(
            [row["publication_seq"] for row in rows], reverse=True
        )
        assert await store.writer.fetchval("SELECT count(*) FROM contract_state_history") == 111
        assert await store.writer.fetchval("SHOW tcp_keepalives_idle") == "15"
        server_build = (await store.writer.fetchval("SELECT version()")).lower()
        windows_server = any(name in server_build for name in ("visual c++", "mingw", "windows"))
        # PostgreSQL on Windows does not support TCP_USER_TIMEOUT and reports zero.
        # Check the server platform, which may differ from the test client's platform.
        assert await store.writer.fetchval("SHOW tcp_user_timeout") == (
            "0" if windows_server else "30000"
        )
        assert await store.writer.fetchval("SHOW idle_session_timeout") == "10min"
    finally:
        await runtime.stop()
        await store.close()
