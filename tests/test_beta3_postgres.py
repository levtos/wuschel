"""b3 authoring against isolated PostgreSQL, including compact/reopen boundaries."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy

import asyncpg
import pytest

from core_contracts.clock import FakeClock
from core_contracts.persistence import PostgresStore
from core_contracts.registry import storage_document
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate
from tests.conftest import activate
from tests.test_beta3_authoring import envelope, legacy_import
from tests.test_core import NOW
from tests.test_postgres import MIGRATIONS
from tests.test_postgres import database as database

pytestmark = pytest.mark.postgres


@asynccontextmanager
async def running(database):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    try:
        await runtime.start()
        yield runtime
    finally:
        await runtime.stop()
        await store.close()


async def save_repeatedly(runtime, draft, count=5):
    for _ in range(count):
        draft = await runtime.submit(
            "draft_update", {**draft, "display_name": f"Synthetic save {draft['draft_version']}"}
        )
        assert not runtime.state.tables["registry_draft_version"]  # compacted after each commit
    return draft


async def assert_versions(runtime, draft, versions):
    rows = await runtime.store.writer.fetch(
        "SELECT key,payload FROM registry_draft_version WHERE payload->>'draft_id'=$1",
        draft["draft_id"],
    )
    assert {row["key"] for row in rows} == {f"{draft['draft_id']}:{v}" for v in versions}
    assert len(rows) == len(versions)
    assert {json.loads(row["payload"])["draft_version"] for row in rows} == set(versions)


async def test_five_saves_reopen_and_five_more_archive_each_version_once(database, config):
    async with running(database) as runtime:
        draft = await runtime.submit("draft_create", {"config": config})
        draft = await save_repeatedly(runtime, draft)
        await assert_versions(runtime, draft, range(1, 7))
        first = await runtime.store.get("registry_draft_version", f"{draft['draft_id']}:1")
    async with running(database) as runtime:
        draft = runtime.state.tables["registry_draft"][draft["draft_id"]]
        draft = await save_repeatedly(runtime, draft)
        await assert_versions(runtime, draft, range(1, 12))
        assert await runtime.store.get("registry_draft_version", f"{draft['draft_id']}:1") == first


async def test_b3_activation_replay_two_windows_and_continuation(
    database, config, aiohttp_client, tmp_path
):
    async with running(database) as runtime:
        draft = await runtime.submit("draft_create", {"config": config})
        draft = await save_repeatedly(runtime, draft)
        body = await envelope(runtime, draft)  # includes real preview and confirmations
        result = await runtime.submit("activate", body)
        assert await runtime.submit("activate", body) == result
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM registry_revision") == 1
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM activation_receipt") == 1
        receipt = await runtime.store.get("activation_receipt", body["request_id"])
        window = await runtime.submit("draft_create", {"config": config})
        remote = deepcopy(config)
        remote["sources"][0]["display_name"] = "Synthetic remote change"
        saved = await runtime.submit("draft_update", {**window, "config": remote})
        local = deepcopy(config)
        local["contracts"][0]["display_name"] = "Synthetic local change"
        with pytest.raises(ValueError, match="draft_version_conflict"):
            await runtime.submit("draft_update", {**window, "config": local})
        merged = await runtime.submit(
            "draft_reconcile",
            {**window, "config": local, "expected_server_version": saved["draft_version"]},
        )
        assert not merged["conflicts"]
        saved = await runtime.submit("draft_update", {**saved, **merged})
        assert saved["config"]["sources"][0]["display_name"] == "Synthetic remote change"
        assert saved["config"]["contracts"][0]["display_name"] == "Synthetic local change"
        newer = deepcopy(config)
        newer["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.synthetic_new"
        await activate(runtime, newer)
        session = await runtime.submit("continuation_start", saved)
        assert not session["conflicts"]
        target = await runtime.submit("continuation_finish", session)
        assert target["base_revision"] == 2 and target["lifecycle"] == "open"
        assert target["config"]["bindings"][0]["adapter"]["ha_entity_id"] == "sensor.synthetic_new"
        await save_repeatedly(runtime, target)
        assert await runtime.submit("continuation_finish", session) == target
        assert await runtime.submit("activate", body) == result
        assert await runtime.store.get("activation_receipt", body["request_id"]) == receipt
    async with running(database) as runtime:
        assert await runtime.submit("activate", body) == result
        from tests.test_workbench_api import api_client

        _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
        response = await client.get(
            f"/api/v1/registry/activation-requests/{body['request_id']}", headers=headers
        )
        assert response.status == 200
        assert await response.json() == {
            "status": "completed",
            "receipt": receipt,
            "result": result,
        }
        assert runtime.state.active_revision == 2
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM registry_revision") == 2
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM activation_receipt") == 2
        # The no-effect completion path must also leave its receipt untouched on replay.
        draft = await runtime.submit("draft_create", {"config": newer})
        request = await envelope(runtime, draft)
        completed = await runtime.submit("draft_complete", request)
        await runtime.submit("tick")
        assert await runtime.submit("draft_complete", request) == completed
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM activation_receipt") == 3
        assert await runtime.store.writer.fetchval("SELECT count(*) FROM registry_revision") == 2
        # Existing immutable payloads still reject changes after compact().
        state = runtime.state.clone()
        state.tables["activation_receipt"][body["request_id"]] = {**receipt, "result_revision": 999}
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.store.commit(state)
        assert await runtime.store.get("activation_receipt", body["request_id"]) == receipt


async def seed_legacy(database, old):
    connection = await asyncpg.connect(database, ssl=False)
    try:
        await connection.execute(
            "UPDATE registry_draft SET payload=$2::jsonb WHERE key=$1",
            old["draft_id"],
            json.dumps(old),
        )
        # A pre-b3 database has no saved-version rows for this legacy document.
        await connection.execute(
            "DELETE FROM registry_draft_version WHERE payload->>'draft_id'=$1", old["draft_id"]
        )
    finally:
        await connection.close()


async def test_legacy_import_two_edits_migrates_once_then_saves_repeatedly(database, config):
    async with running(database) as runtime:
        old = await legacy_import(runtime, config)
        base = storage_document(validate(runtime.config.model_dump(mode="json"), runtime.types))
        expected = deepcopy(base)
        assert len(expected["sources"]) == 2
        for source in expected["sources"]:
            source["freshness"]["interval_s"] = 7200
        assert old["config"] == expected
    await seed_legacy(database, old)
    async with running(database) as runtime:
        original = runtime.state.tables["registry_draft"][old["draft_id"]]
        assert original["lifecycle"] == "continued" and original["completed_revision"] == 2
        assert original["portable"]["status"] == "consumed"
        target = runtime.state.tables["registry_draft"][original["continued_to_draft_id"]]
        assert target["base_revision"] == 2 and target["lifecycle"] == "open"
        assert target["config"] == expected and "portable" not in target
        await assert_versions(runtime, original, [old["draft_version"]])
        await assert_versions(runtime, target, [1])
        before = deepcopy(runtime.state.tables["registry_draft"])
        sessions = deepcopy(runtime.state.tables["draft_continuation"])
    async with running(database) as runtime:
        assert runtime.state.tables["registry_draft"] == before
        assert runtime.state.tables["draft_continuation"] == sessions
        assert runtime.state.active_revision == 2
        await assert_versions(runtime, original, [old["draft_version"]])
        target = await save_repeatedly(runtime, target)
        await assert_versions(runtime, target, range(1, 7))
        assert target["config"] == expected and "portable" not in target


async def test_existing_b3_current_version_is_backfilled_only_once(database, config):
    async with running(database) as runtime:
        draft = await runtime.submit("draft_create", {"config": config})
        second = await runtime.submit("draft_create", {"config": config})
    await seed_legacy(database, draft)  # authoring_version=2, but missing archive row
    await seed_legacy(database, second)
    for _ in range(2):
        async with running(database) as runtime:
            await assert_versions(runtime, draft, [1])
            await assert_versions(runtime, second, [1])
    async with running(database) as runtime:
        draft = await save_repeatedly(runtime, draft)
        await assert_versions(runtime, draft, range(1, 7))


@pytest.mark.parametrize("damage", ["package", "version"])
async def test_unexpected_corrupt_legacy_exception_does_not_prevent_start(
    database, config, caplog, damage
):
    async with running(database) as runtime:
        old = await legacy_import(runtime, config)
        healthy = await runtime.submit("draft_create", {"config": config})
        healthy.pop("authoring_version")
    old["portable"]["package"] = []  # effects_match raises AttributeError, not ValueError
    if damage == "version":
        old.pop("draft_version")  # The failure marker must survive the final startup commit too.
    await seed_legacy(database, old)
    await seed_legacy(database, healthy)
    async with running(database) as runtime:
        assert runtime.task is not None and not runtime.task.done()
        draft = runtime.state.tables["registry_draft"][old["draft_id"]]
        assert draft["lifecycle"] == "open" and draft["base_status"] == "needs_review"
        assert draft["config"] == old["config"] and "continued_to_draft_id" not in draft
        assert runtime.state.tables["registry_draft"][healthy["draft_id"]]["base_status"] == "known"
        await assert_versions(runtime, healthy, [1])
        events = [r for r in caplog.records if r.msg == "draft_migration_failed"]
        assert len(events) == 1
        assert events[0].draft_id == old["draft_id"] and events[0].error_type == "AttributeError"
        assert not events[0].exc_info and not events[0].args
        assert "portable" not in events[0].__dict__ and "config" not in events[0].__dict__
