"""Real PostgreSQL v3→v4 editing/activation, archives, and restart compatibility."""

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.freshness_templates import upgrade_document
from core_contracts.persistence import PostgresStore
from core_contracts.registry import storage_document
from core_contracts.runtime import Runtime
from tests.conftest import activate, activate_draft
from tests.test_beta3_postgres import assert_versions, save_repeatedly
from tests.test_beta4_freshness import legacy, shared
from tests.test_beta_openings import closed, field
from tests.test_core import NOW
from tests.test_postgres import MIGRATIONS
from tests.test_postgres import database as database

pytestmark = pytest.mark.postgres


@asynccontextmanager
async def running(database):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry(include_test_types=True))
    try:
        await runtime.start()
        yield runtime
    finally:
        await runtime.stop()
        await store.close()


async def test_schema_and_fingerprint_3_to_4_restart_twice_and_archive_collisions(database):
    async with running(database) as runtime:
        await activate(runtime, legacy())
        for index in range(4):
            await closed(runtime, index)
        observations = deepcopy(runtime.state.tables["source_observation_current"])
        draft = await runtime.submit("draft_create", {"config": shared(legacy())})
        draft = await save_repeatedly(runtime, draft)
        await activate_draft(runtime, draft)
        assert runtime.config.config_schema_version == runtime.config.fingerprint_version == 4
        await assert_versions(runtime, draft, range(1, 7))
        revision = deepcopy(runtime.state.tables["registry_revision"]["2"])
        for key, row in observations.items():
            assert (
                runtime.state.tables["source_observation_current"][key]["ingest_seq"]
                == row["ingest_seq"]
            )
        editable = await runtime.submit(
            "draft_create", {"config": storage_document(runtime.config)}
        )
        editable = await save_repeatedly(runtime, editable)
        config = deepcopy(editable["config"])
    for restart in range(2):
        async with running(database) as runtime:
            assert runtime.state.active_revision == 2
            assert runtime.state.tables["registry_revision"]["2"] == revision
            assert (
                runtime.state.tables["registry_draft"][draft["draft_id"]]["lifecycle"]
                == "completed"
            )
            editable = runtime.state.tables["registry_draft"][editable["draft_id"]]
            assert editable["config"] == config
            editable = await save_repeatedly(runtime, editable)
            await assert_versions(runtime, editable, range(1, 12 + restart * 5))
            assert field(runtime)["status"] == "valid"  # Proven MQTT device time survives restart.
            for key, row in observations.items():
                current = runtime.state.tables["source_observation_current"][key]
                assert current["ingest_seq"] == row["ingest_seq"]
                assert current["device_time"] == row["device_time"]


async def test_first_start_preserves_active_revision_and_all_b3_draft_lifecycles(database):
    async with running(database) as runtime:
        await activate(runtime, legacy())
        original = storage_document(runtime.config)
        completed = await runtime.submit("draft_create", {"config": original})
        await activate_draft(runtime, completed)
        opened = await runtime.submit("draft_create", {"config": original})
        stale = await runtime.submit("draft_create", {"config": original})
        updated = deepcopy(original)
        updated["sources"][0]["display_name"] = "Synthetic newer source"
        await activate(runtime, updated)
        session = await runtime.submit("continuation_start", stale)
        continued = await runtime.submit("continuation_finish", session)
        rows = deepcopy(runtime.state.tables["registry_draft"])
        active = deepcopy(runtime.state.tables["registry_revision"]["3"])
    for _ in range(2):
        async with running(database) as runtime:
            assert runtime.state.active_revision == 3
            assert runtime.state.tables["registry_revision"]["3"] == active
            assert runtime.state.tables["registry_draft"] == rows
            assert runtime.state.tables["registry_draft"][opened["draft_id"]]["lifecycle"] == "open"
            assert (
                runtime.state.tables["registry_draft"][stale["draft_id"]]["lifecycle"]
                == "continued"
            )
            assert (
                runtime.state.tables["registry_draft"][completed["draft_id"]]["lifecycle"]
                == "completed"
            )
            assert (
                runtime.state.tables["registry_draft"][continued["draft_id"]]["lifecycle"] == "open"
            )
    async with running(database) as runtime:
        edited = await runtime.submit(
            "draft_update", {**continued, "config": upgrade_document(continued["config"])}
        )
        edited = await save_repeatedly(runtime, edited)
        await assert_versions(runtime, edited, range(1, 8))


@pytest.mark.parametrize("choice", ["draft", "current"])
async def test_b3_draft_continues_after_active_schema4_with_explicit_union_choice(database, choice):
    async with running(database) as runtime:
        base = legacy()
        await activate(runtime, base)
        changed = deepcopy(base)
        changed["sources"][0]["freshness"]["interval_s"] = 9000
        old = await runtime.submit("draft_create", {"config": changed})
        await activate(runtime, shared(base))
        session = await runtime.submit("continuation_start", old)
        assert len(session["conflicts"]) == 1
        row = session["conflicts"][0]
        assert row["path"].endswith("/freshness_selection")
        session = await runtime.submit(
            "continuation_decisions",
            {
                **session,
                "decisions": {row["path"]: {"digests": row["digests"], "choice": choice}},
            },
        )
        assert not session["conflicts"]
        target = await runtime.submit("continuation_finish", session)
        assert target["base_revision"] == 2
        assert target["config"]["config_schema_version"] == 4
        source = target["config"]["sources"][0]
        assert "freshness" not in source
        assert source["freshness_selection"]["kind"] == ("own" if choice == "draft" else "shared")
        if choice == "draft":
            assert source["freshness_selection"]["rule"]["interval_s"] == 9000
        assert runtime.state.tables["registry_draft"][old["draft_id"]]["config"] == changed
        target = await save_repeatedly(runtime, target)
        await assert_versions(runtime, target, range(1, 7))
