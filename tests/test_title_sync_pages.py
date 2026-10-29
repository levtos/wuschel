"""Synthetic scale, actual HTTP responsiveness and page/ownership guarantees."""

import asyncio
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from core_contracts import title_reader, title_sync
from core_contracts.clock import SystemClock
from core_contracts.managed_catalog import head
from core_contracts.model import canonical
from core_contracts.title_sync_runs import cancel, page_view

from .conftest import activate
from .test_beta_media_import import document
from .test_title_reader import Database, base, credentials
from .test_title_sync import ACTOR, apply, inspect, mapping, observed

pytest_plugins = ["tests.test_postgres"]


def population(count=3500):
    rows = []
    for index in range(count):
        master = rows[(index // 10) * 10] if index % 10 else None
        rows.append(
            observed(
                f"Synthetic family {index // 10:04} item {index % 10}",
                parent_key=master["normalized_key"] if master else None,
                parent_id=master["id"] if master else None,
                hidden_at="2032-01-01T00:00:00Z" if index % 11 == 0 else None,
            )
        )
    for row in rows[:100]:
        row["contexts"].append({"context": "second", "source_app": ""})
    return rows


def targets():
    result = mapping()
    result["mappings"][canonical(["second", ""])] = next(iter(result["mappings"].values())).copy()
    return result


class LargeDatabase(Database):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    async def cursor(self, sql, **kwargs):
        assert self.entered
        self.queries.append(sql)
        for row in self.rows:
            if sql == title_reader.CATALOG_SQL:
                yield {
                    **{
                        k: v
                        for k, v in row.items()
                        if k not in {"contexts", "telemetry", "parent_key"}
                    },
                    **row["telemetry"],
                    "album": None,
                    "app_name": None,
                    "reviewed_at": None,
                }
            else:
                assert sql == title_reader.CONTEXT_SQL
                for context in row["contexts"]:
                    yield {
                        "entry_id": row["id"],
                        "enum_override": None,
                        **context,
                        **row["telemetry"],
                    }


async def test_3500_titles_3600_contexts_budget_and_parallel_http_during_plan_and_apply(
    runtime, config, aiohttp_client, tmp_path, monkeypatch
):
    from .test_workbench_api import api_client

    await activate(runtime, document(config))
    rows = population()
    assert len(rows) == 3500 and sum(len(row["contexts"]) for row in rows) == 3600
    db = LargeDatabase(rows)
    monkeypatch.setattr(title_reader.asyncpg, "connect", AsyncMock(return_value=db))
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    api.title_reader = lambda: title_reader.read(base(), credentials())
    delays = []

    async def concurrent(request):
        task = asyncio.create_task(request)
        while not task.done():
            started = time.perf_counter()
            response = await client.get("/api/v1/snapshot", headers=headers)
            assert response.status == 200
            await response.read()
            delays.append(time.perf_counter() - started)
            await SystemClock().sleep(0.05)
        return await task

    start = time.perf_counter()
    response = await concurrent(
        client.post(
            "/api/v1/title-sync/preview",
            headers=headers,
            json={
                "expected_active_revision": runtime.state.active_revision,
                "mapping": targets(),
                "expected_mapping_version": 0,
            },
        )
    )
    assert response.status == 200, await response.text()
    run = await response.json()
    planning = time.perf_counter() - start
    assert planning < 10
    assert db.closed and db.queries == [title_reader.CATALOG_SQL, title_reader.CONTEXT_SQL]
    assert run["page_count"] == 10 and run["page_source_rows"] == 350
    applying = []
    for page in range(3):
        start = time.perf_counter()
        response = await concurrent(
            client.post(
                "/api/v1/title-sync/apply",
                headers=headers,
                json={
                    "plan_id": run["plan_id"],
                    "plan_digest": run["plan_digest"],
                    "request_id": str(uuid4()),
                },
            )
        )
        assert response.status == 200, await response.text()
        applying.append(time.perf_counter() - start)
        assert applying[-1] < 5
        page_response = await client.get(
            "/api/v1/title-sync/page", headers=headers, params={"plan_id": run["plan_id"]}
        )
        run = await page_response.json()
        assert run["page"] == page + 2
    assert max(delays) < 1
    assert runtime.persistence
    cancel(runtime, ACTOR, run["plan_id"])
    assert run["plan_id"] not in runtime.title_sync_plans
    resumed = await inspect(runtime, rows)
    assert resumed["counts"] == {"unchanged": 1050, "new": 2450}
    assert resumed["page_count"] == 7
    while True:
        start = time.perf_counter()
        result = await concurrent(apply(runtime, resumed))
        applying.append(time.perf_counter() - start)
        assert applying[-1] < 5
        if result["complete"]:
            break
        resumed = page_view(runtime, ACTOR, resumed["plan_id"])
    assert len(head(runtime.state, runtime.config.catalogs[0])["entries"]) == 7000
    assert (await inspect(runtime, rows))["counts"] == {"unchanged": 3500}
    print(
        f"TC scale: read+plan={planning:.3f}s max page={max(applying):.3f}s max parallel HTTP={max(delays):.3f}s"
    )


async def test_families_stable_two_tabs_and_snapshot_replanning(runtime, config):
    doc = document(config)
    doc["catalogs"][0]["classification_schema"]["legend"] = {
        value: {"display_name": f"Synthetic {value}", "description": "Synthetic class description"}
        for value in doc["catalogs"][0]["classification_schema"]["values"]
    }
    await activate(runtime, doc)
    rows = population(700)
    first = await inspect(runtime, rows, targets())
    second = await inspect(runtime, rows, targets())
    assert first["plan_id"] != second["plan_id"]
    assert first["groups"] == second["groups"]
    primary_ids = {
        item["after"]["tc_metadata"]["source_id"]
        for item in page_view(runtime, ACTOR, first["plan_id"])["items"]
    }
    assert all(
        item["new_class"].startswith("Synthetic ")
        for item in page_view(runtime, ACTOR, first["plan_id"])["items"]
    )
    visible = page_view(runtime, ACTOR, first["plan_id"])["items"]
    for index in range(0, len(visible), 10):
        family = visible[index : index + 10]
        master = family[0]["after"]["tc_metadata"]
        assert master.get("parent_id") is None
        assert all(
            row["after"]["tc_metadata"]["parent_id"] == master["source_id"] for row in family[1:]
        )
    for index in range(0, 700, 10):
        family_ids = {row["id"] for row in rows[index : index + 10]}
        assert not (primary_ids & family_ids) or family_ids <= primary_ids
    cancel(runtime, ACTOR, second["plan_id"])
    await apply(runtime, first)
    assert first["plan_id"] in runtime.title_sync_plans
    with pytest.raises(ValueError, match="plan_conflict"):
        page_view(runtime, {"kind": "other"}, first["plan_id"])


async def test_configuration_resolution_is_constant_per_plan(runtime, config, monkeypatch):
    await activate(runtime, document(config))
    original = runtime._config
    original_definition, original_head = title_sync.definition, title_sync.head
    calls = []
    definitions, heads = [], []

    def counted(state):
        calls.append(1)
        return original(state)

    monkeypatch.setattr(runtime, "_config", counted)

    def counted_definition(*args):
        definitions.append(1)
        return original_definition(*args)

    def counted_head(*args):
        heads.append(1)
        return original_head(*args)

    monkeypatch.setattr(title_sync, "definition", counted_definition)
    monkeypatch.setattr(title_sync, "head", counted_head)
    for count in (200, 400):
        calls.clear()
        definitions.clear()
        heads.clear()
        title_sync.plan(
            runtime,
            runtime.state,
            {"version": 3, "entries": population(count)},
            {
                "expected_active_revision": runtime.state.active_revision,
                "mapping": targets(),
                "expected_mapping_version": 0,
            },
        )
        assert len(calls) == 1
        assert len(definitions) == len(heads) == 1


async def test_two_tab_runs_replan_their_own_snapshot_without_rereading_and_conflict_explicitly(
    runtime, config, aiohttp_client, tmp_path
):
    from .test_workbench_api import api_client

    await activate(runtime, document(config))
    rows = population(700)
    calls = 0

    async def reader():
        nonlocal calls
        calls += 1
        return {"version": 3, "entries": rows}

    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    api.title_reader = reader

    async def http_preview(body):
        response = await client.post("/api/v1/title-sync/preview", headers=headers, json=body)
        assert response.status == 200, await response.text()
        return await response.json()

    body = {
        "expected_active_revision": runtime.state.active_revision,
        "mapping": targets(),
        "expected_mapping_version": 0,
    }
    first = await http_preview(body)
    second = await http_preview(body)
    rows[0]["enum"] = 2
    await apply(runtime, first)
    assert page_view(runtime, ACTOR, second["plan_id"])["page"] == 1
    with pytest.raises(ValueError, match="title_sync_mapping_conflict"):
        await apply(runtime, second)
    runtime.clock.advance(1)
    replanned = await http_preview(
        {
            "plan_id": second["plan_id"],
            "expected_active_revision": runtime.state.active_revision,
        },
    )
    assert calls == 2
    assert replanned["at"] == second["at"]
    assert replanned["counts"] == {"unchanged": 350, "new": 350}
    assert replanned["page_source_rows"] == 350
    assert first["plan_id"] in runtime.title_sync_plans
    await apply(runtime, replanned)
    assert "_snapshot" not in runtime.title_sync_plans[second["plan_id"]]


async def test_run_capacity_rejects_without_replacing_other_tabs(runtime, config, monkeypatch):
    from core_contracts import title_sync_runs

    await activate(runtime, document(config))
    monkeypatch.setattr(title_sync_runs, "MAX_RUNS", 2)
    first = await inspect(runtime, [observed()], mapping())
    second = await inspect(runtime, [observed()], mapping())
    with pytest.raises(ValueError, match="title_sync_run_limit"):
        await inspect(runtime, [observed()], mapping())
    assert set(runtime.title_sync_plans) == {first["plan_id"], second["plan_id"]}
    cancel(runtime, ACTOR, first["plan_id"])
    third = await inspect(runtime, [observed()], mapping())
    assert set(runtime.title_sync_plans) == {second["plan_id"], third["plan_id"]}


async def test_page_shows_missing_old_class_as_empty_but_tc_zero_as_valid_class(runtime, config):
    await activate(runtime, document(config))
    row = observed(enum=0)
    run = await inspect(runtime, [row], mapping())
    await apply(runtime, run)
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": "example.titles",
                "definition_version": 1,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": 1,
                "request_id": str(uuid4()),
                "reason": "Synthetic unassigned entry",
                "changes": [
                    {
                        "scope": "synthetic",
                        "media_type": "music",
                        "signal_type": "title",
                        "key": canonical([row["artist"], row["title"]]),
                        "classification": None,
                    }
                ],
            }
        },
    )
    run = await inspect(runtime, [row])
    item = page_view(runtime, ACTOR, run["plan_id"])["items"][0]
    assert item["old_class"] == "–" and item["new_class"] == "normal"


async def test_abort_during_reader_or_preparation_never_commits_and_frees_snapshot(
    runtime, config, monkeypatch
):
    from concurrent.futures import Future

    from core_contracts import title_sync_runs

    await activate(runtime, document(config))
    started = asyncio.Event()
    finish = asyncio.Event()

    async def reader():
        started.set()
        await finish.wait()
        return {"version": 3, "entries": [observed()]}

    run_id = str(uuid4())
    work = asyncio.create_task(
        runtime.submit(
            "title_sync_preview",
            {
                "run_id": run_id,
                "expected_active_revision": runtime.state.active_revision,
                "_reader": reader,
                "_actor": ACTOR,
            },
        )
    )
    await started.wait()
    cancel(runtime, ACTOR, run_id)
    finish.set()
    with pytest.raises(ValueError, match="plan_conflict"):
        await work
    assert not runtime.state.tables["catalog_head"]
    run = await inspect(runtime, [observed()], mapping())
    original = title_sync_runs.prepare_page
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    release = Future()

    def blocked(*args):
        loop.call_soon_threadsafe(entered.set)
        release.result(timeout=5)
        return original(*args)

    monkeypatch.setattr(title_sync_runs, "prepare_page", blocked)
    work = asyncio.create_task(apply(runtime, run))
    await entered.wait()
    cancel(runtime, ACTOR, run["plan_id"])
    release.set_result(None)
    with pytest.raises(ValueError, match="plan_conflict"):
        await work
    assert not runtime.state.tables["catalog_head"]
    assert not runtime.state.tables["title_sync_mapping"]


async def test_oversized_family_is_not_split_and_errors_remain_grouped(runtime, config):
    await activate(runtime, document(config))
    master = observed("Synthetic Oversized Master")
    rows = [master] + [
        observed(
            f"Synthetic Oversized {index}",
            parent_key=master["normalized_key"],
            parent_id=master["id"],
        )
        for index in range(350)
    ]
    run = await inspect(runtime, rows, mapping())
    assert run["counts"] == {"unmappable": 351}
    assert not run["can_apply"]
    assert run["error_groups"][0]["reason"] == "title_sync_family_limit"
    assert run["error_groups"][0]["count"] == 351
    assert not page_view(runtime, ACTOR, run["plan_id"])["items"]
    invalid = await inspect(runtime, [None, "Synthetic invalid", {}], mapping())
    assert invalid["counts"] == {"unmappable": 3} and not invalid["can_apply"]


@pytest.mark.postgres
async def test_postgres_pages_keep_committed_pages_on_atomic_failure_and_stay_responsive(
    database, config, aiohttp_client, tmp_path
):
    from pathlib import Path

    from core_contracts.clock import FakeClock
    from core_contracts.persistence import PostgresStore
    from core_contracts.runtime import Runtime
    from core_contracts.testing.contract_types import type_registry

    from .test_core import NOW
    from .test_workbench_api import api_client

    store = PostgresStore(database, Path(__file__).resolve().parents[1] / "migrations", tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await activate(runtime, document(config))
        _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
        run = await inspect(runtime, population(700), targets())
        start = time.perf_counter()
        task = asyncio.create_task(apply(runtime, run))
        while not task.done():
            request_start = time.perf_counter()
            response = await client.get("/api/v1/snapshot", headers=headers)
            assert response.status == 200
            await response.read()
            assert time.perf_counter() - request_start < 1
            await SystemClock().sleep(0.01)
        await task
        assert time.perf_counter() - start < 5
        assert not store.writer.is_closed() and runtime.persistence
        assert len(head(runtime.state, runtime.config.catalogs[0])["entries"]) == 700

        def fail():
            raise RuntimeError("synthetic transaction failure")

        store.before_commit = fail
        with pytest.raises(RuntimeError, match="synthetic transaction failure"):
            await apply(runtime, run)
        assert len(head(runtime.state, runtime.config.catalogs[0])["entries"]) == 700
        saved = await store.load()
        assert len(next(iter(saved.tables["catalog_head"].values()))["entries"]) == 700
        assert len(saved.tables["title_sync_mapping"]) == 1
        store.before_commit = None
        await runtime.submit("recover", {})
        await apply(runtime, run)
        assert len(head(runtime.state, runtime.config.catalogs[0])["entries"]) == 1400
    finally:
        store.before_commit = None
        await runtime.stop()
        await store.close()
