"""b22 P2: synthetic size, isolation and transaction gates; execute after the last package."""

import asyncio
import copy
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

import pytest
from pydantic import Field

from core_contracts.api import API
from core_contracts.clock import FakeClock
from core_contracts.evidence import Observation
from core_contracts.extensions.catalog.catalog_care import listing
from core_contracts.extensions.catalog.managed_catalog import head, head_key, identity, prepare
from core_contracts.history import HistoryQuery
from core_contracts.persistence import MemoryStore, PostgresStore, State
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.testing.contract_types import type_registry

from .conftest import activate
from .test_core import NOW
from .test_declarative import literal, schema
from .test_managed_catalog import catalog, lookup_rule
from .test_postgres import MIGRATIONS
from .test_postgres import database as database


class MeasuredClock(FakeClock):
    """Frozen wall time and real phase timings; no fake zero-duration benchmark."""

    def monotonic(self):
        return perf_counter()


class SeedWrite(CatalogWrite):
    changes: tuple[EntryChange, ...] = Field(min_length=1, max_length=6000)


def document(config):
    result = copy.deepcopy(config)
    result.update(
        config_schema_version=3,
        fingerprint_version=3,
        definitions=[],
        contracts=[],
        sources=[],
        bindings=[],
        catalogs=[catalog(), {**catalog(), "catalog_id": "example.unrelated"}],
    )
    for index in range(85):
        name = f"example.lookup{index}"
        lookup = lookup_rule(f"Synthetic {index:05}")
        temporal = {
            "op": "temporal",
            "id": "timer",
            "operation": "stable_for",
            "duration_s": 60,
            "accepts_held": False,
            "args": [
                {
                    "op": "compare",
                    "comparison": "eq",
                    "left": lookup,
                    "right": {
                        **literal("other", "enum"),
                        "schema": schema("enum", value_catalog=["other", "example"]),
                    },
                }
            ],
        }
        result["definitions"].append(
            {
                "type_id": name,
                "type_version": 1,
                "language_version": 4,
                "inputs": [{"name": "a", "schema": schema()}],
                "schema": {
                    "fields": {
                        "value": schema("enum", value_catalog=["other", "example"]),
                        "stable": schema(),
                    },
                    "available_projection": ["value"],
                },
                "outputs": {"value": lookup, "stable": temporal},
            }
        )
        result["sources"].append(
            {
                **copy.deepcopy(config["sources"][0]),
                "source_id": f"source.synthetic{index}",
                "physical_source_key": f"synthetic.{index}",
            }
        )
        result["bindings"].append(
            {
                **copy.deepcopy(config["bindings"][0]),
                "binding_id": f"binding.synthetic{index}",
                "source_id": f"source.synthetic{index}",
                "adapter": {"kind": "ha_state", "ha_entity_id": f"binary_sensor.synthetic_{index}"},
            }
        )
        result["contracts"].append(
            {
                "contract_id": name,
                "type_id": name,
                "type_version": 1,
                "enabled": True,
                "inputs": [{"name": "a", "ref": f"source.synthetic{index}"}],
                "parameters": {},
            }
        )
    return result


async def populated(runtime, config, *, large=True):
    await activate(runtime, document(config))
    runtime.ha = runtime.bridge = "connected"
    runtime.first_snapshot = True
    state = runtime.state.clone()
    request = SeedWrite.model_validate(
        {
            "catalog_id": "example.titles",
            "definition_version": 1,
            "expected_active_revision": state.active_revision,
            "expected_content_revision": 0,
            "request_id": str(uuid4()),
            "reason": "Synthetic benchmark fixture",
            "changes": [
                {
                    "scope": "example",
                    "media_type": "music",
                    "signal_type": "title",
                    "key": f"Synthetic {index:05}",
                    "display_name": f"Synthetic {index:05}",
                    "classification": "other",
                }
                for index in range(6000 if large else 85)
            ],
        }
    )
    for offset in range(0, len(request.changes), 1000):
        prepare(
            state,
            runtime.config,
            request.model_copy(
                update={
                    "changes": request.changes[offset : offset + 1000],
                    "expected_content_revision": offset // 1000,
                    "request_id": str(uuid4()),
                }
            ),
            {"kind": "synthetic"},
            runtime.clock.now_utc(),
        )
    prepare(
        state,
        runtime.config,
        request.model_copy(
            update={"catalog_id": "example.unrelated", "changes": request.changes[:1]}
        ),
        {"kind": "synthetic"},
        runtime.clock.now_utc(),
    )
    for change in request.changes:
        key = identity(change.scope, change.media_type, change.signal_type, change.key)
        state.tables["catalog_sighting"][head_key(runtime.config.catalogs[0]) + ":" + key] = {
            "catalog": head_key(runtime.config.catalogs[0]),
            "identity": key,
            "scope": change.scope,
            "media_type": change.media_type,
            "signal_type": change.signal_type,
            "key": change.key,
            "first_seen": (NOW - timedelta(days=2)).isoformat(),
            "last_seen": (NOW - timedelta(days=1)).isoformat(),
            "seen_count": 3,
        }
    # A second catalog and sizeable saved draft are unrelated and must stay shared
    # read-only in the writer fork; neither belongs to the care changeset.
    state.tables["registry_draft"]["synthetic.unrelated"] = {
        "draft_id": "synthetic.unrelated",
        "draft_version": 1,
        "config": document(config),
        "display_name": "Synthetic unrelated draft",
        "lifecycle": "open",
        "updated_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
    }
    for index in range(85):
        for report in range(240 if large else 1):
            observation = Observation(
                source_id=f"source.synthetic{index}",
                binding_id=f"binding.synthetic{index}",
                adapter="ha_state",
                value_raw=True,
                received_at=NOW - timedelta(seconds=240 - report),
                ha_time_fired=NOW - timedelta(seconds=240 - report),
                observation_kind="live_change",
                epoch_id=state.epoch_id,
                ingest_seq=report,
                registry_revision=state.active_revision,
                reference_version=2,
            )
            state.tables["source_observation_history"][observation.reference] = (
                observation.storage_document()
            )
        state.tables["source_observation_current"][observation.binding_id] = (
            observation.storage_document()
        )
    runtime._evaluate(state, runtime.config, origin="revision")
    await runtime._commit(state, publication=True)
    return runtime.config.catalogs[0]


def care(runtime, value="example", **patch):
    return {
        "catalog_id": "example.titles",
        "definition_version": 1,
        "expected_active_revision": runtime.state.active_revision,
        "expected_content_revision": head(runtime.state, runtime.config.catalogs[0])["revision"],
        "request_id": str(uuid4()),
        "reason": "Synthetic measured care",
        "action": "group",
        "identities": [
            identity("example", "music", "title", f"Synthetic {index:05}") for index in (0, 1)
        ],
        "master": identity("example", "music", "title", "Synthetic 00000"),
        "classification": value,
        **patch,
    }


async def benchmark(runtime, config, aiohttp_client, tmp_path, record_property):
    catalog = await populated(runtime, config)
    assert len(head(runtime.state, catalog)["entries"]) == 6000
    assert len(runtime.config.contracts) == 85
    assert (
        len(
            (await runtime.store.history("source_observation_history", HistoryQuery(limit=1)))[
                "items"
            ]
        )
        == 1
    )
    assert len(runtime.state.tables["catalog_sighting"]) == 6000
    client = await aiohttp_client(
        API(runtime, Tokens(tmp_path), str(uuid4()), ingress_address="127.0.0.1").application(
            ingress=True
        )
    )
    for index in range(3):
        payload = care(runtime, "example" if index % 2 == 0 else "other")
        for operation in ("preview", "write"):
            started = perf_counter()
            response = await client.post("/api/v1/catalogs/care/" + operation, json=payload)
            result = await response.json()
            elapsed = perf_counter() - started
            assert response.status == 200, result
            record_property(
                f"{type(runtime.store).__name__}.{operation}.{index}.ms", elapsed * 1000
            )
            phases = dict(runtime.catalog_care_timings)
            record_property(f"{type(runtime.store).__name__}.{operation}.{index}.phases", phases)
            assert elapsed < 0.5, (operation, elapsed, phases)
            assert phases["state_copy_ms"] > 0
        started = perf_counter()
        response = await client.get(
            "/api/v1/catalogs/care?catalog_id=example.titles&definition_version=1&sort=newest"
        )
        page = await response.json()
        assert response.status == 200 and page["total"] == 6000 and len(page["items"]) == 100
        assert perf_counter() - started < 0.5


async def test_memory_6000_entries_85_contracts_with_measurement_stock_under_500ms(
    config, aiohttp_client, tmp_path, record_property
):
    runtime = Runtime(MemoryStore(), MeasuredClock(NOW), type_registry())
    await runtime.start()
    try:
        await benchmark(runtime, config, aiohttp_client, tmp_path, record_property)
    finally:
        await runtime.stop()


@pytest.mark.postgres
async def test_postgres_6000_entries_85_contracts_with_measurement_stock_under_500ms(
    database, config, aiohttp_client, tmp_path, record_property
):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, MeasuredClock(NOW), type_registry())
    await runtime.start()
    try:
        await benchmark(runtime, config, aiohttp_client, tmp_path, record_property)
    finally:
        await runtime.stop()
        await store.close()


async def test_care_never_full_clones_and_preserves_unaffected_a06_nodes(
    runtime, config, monkeypatch
):
    catalog = await populated(runtime, config, large=False)
    before = runtime.state
    untouched = {
        key: copy.deepcopy(value)
        for key, value in before.tables["node_state"].items()
        if key not in {"example.lookup0", "example.lookup1"}
    }
    payload = care(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(
            State, "clone", lambda self: pytest.fail("care performed a full runtime clone")
        )
        preview = await runtime.submit("catalog_care_preview", {"request": payload})
        assert runtime.state is before
        assert preview["affected_contracts"] == 2
        await runtime.submit("catalog_care_write", {"request": payload})
    assert (
        runtime.state.tables["registry_draft"]["synthetic.unrelated"]
        is before.tables["registry_draft"]["synthetic.unrelated"]
    )
    for key, value in untouched.items():
        assert runtime.state.tables["node_state"][key] is before.tables["node_state"][key]
        assert (
            runtime.state.tables["node_state"][key] == value
        )  # Baselines/deadlines stay byte-equivalent.
    unrelated = head_key(runtime.config.catalogs[1])
    assert (
        runtime.state.tables["catalog_head"][unrelated] is before.tables["catalog_head"][unrelated]
    )
    assert head(runtime.state, catalog)["revision"] == head(before, catalog)["revision"] + 1
    assert (
        runtime.state.tables["contract_state_current"]["example.lookup1"]["fields"]["value"][
            "value"
        ]
        == "example"
    )


async def failure_probe(runtime, config):
    catalog = await populated(runtime, config, large=False)
    before = runtime.state
    tables = copy.deepcopy(before.tables)
    payload = care(runtime)

    def fail():
        assert runtime.state is before
        assert head(runtime.state, catalog)["revision"] == 1
        assert (
            runtime.snapshot()["contracts"]["example.lookup1"]["fields"]["value"]["value"]
            == "other"
        )
        raise RuntimeError("synthetic care commit failure")

    runtime.store.before_commit = fail
    try:
        with pytest.raises(RuntimeError, match="synthetic care commit failure"):
            await runtime.submit("catalog_care_write", {"request": payload})
    finally:
        runtime.store.before_commit = None
    assert runtime.state is before and runtime.state.tables == tables
    saved = await runtime.store.load()
    assert head(saved, catalog)["revision"] == 1
    assert (
        saved.tables["contract_state_current"]["example.lookup1"]["fields"]["value"]["value"]
        == "other"
    )
    assert await runtime.store.get("catalog_request", payload["request_id"]) is None
    await runtime.submit("recover", {})
    result = await runtime.submit("catalog_care_write", {"request": payload})
    saved = await runtime.store.load()
    assert head(saved, catalog)["revision"] == result["content_revision"] == 2
    assert (
        saved.tables["contract_state_current"]["example.lookup1"]["fields"]["value"]["value"]
        == "example"
    )
    assert await runtime.submit("catalog_care_write", {"request": payload}) == result
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await runtime.submit(
            "catalog_care_write", {"request": {**payload, "request_id": str(uuid4())}}
        )


async def test_memory_commit_failure_keeps_all_care_state_and_replays(runtime, config):
    await failure_probe(runtime, config)


@pytest.mark.postgres
async def test_postgres_commit_failure_keeps_all_care_state_and_replays(database, config):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    try:
        await failure_probe(runtime, config)
    finally:
        await runtime.stop()
        await store.close()


async def test_readers_see_old_head_and_result_until_commit_then_both_new(
    runtime, config, monkeypatch
):
    catalog = await populated(runtime, config, large=False)
    original_commit = runtime.store.commit
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(state):
        entered.set()
        await release.wait()
        await original_commit(state)

    payload = care(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.store, "commit", gated)
        writing = asyncio.create_task(runtime.submit("catalog_care_write", {"request": payload}))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert head(runtime.state, catalog)["revision"] == 1
            assert (
                runtime.snapshot()["contracts"]["example.lookup1"]["fields"]["value"]["value"]
                == "other"
            )
            loaded = await runtime.store.load()
            assert head(loaded, catalog)["revision"] == 1
            assert (
                loaded.tables["contract_state_current"]["example.lookup1"]["fields"]["value"][
                    "value"
                ]
                == "other"
            )
        finally:
            release.set()
            await writing
    assert head(runtime.state, catalog)["revision"] == 2
    assert (
        runtime.snapshot()["contracts"]["example.lookup1"]["fields"]["value"]["value"] == "example"
    )


async def test_listing_builds_rows_once_and_keeps_paging_filters(runtime, config, monkeypatch):
    import core_contracts.extensions.catalog.catalog_care as module

    catalog = await populated(runtime, config, large=False)
    original, calls = module.all_rows, []

    def once(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(module, "all_rows", once)
    first = listing(runtime.state, catalog, limit=20, sort="title")
    assert len(calls) == 1 and first["total"] == 85 and len(first["items"]) == 20
    second = listing(runtime.state, catalog, limit=20, sort="title", after=first["next_cursor"])
    assert len(calls) == 2
    assert {row["identity"] for row in first["items"]}.isdisjoint(
        row["identity"] for row in second["items"]
    )
    assert first["filter_values"] == {
        "scope": ["example"],
        "media_type": ["music"],
        "signal_type": ["title"],
    }


async def test_evaluation_failure_discards_detached_nodes_latches_and_catalog(
    runtime, config, monkeypatch
):
    await populated(runtime, config, large=False)
    before, tables = runtime.state, copy.deepcopy(runtime.state.tables)
    latches = copy.deepcopy(runtime.latches)
    evaluate = runtime._evaluate

    def failing(*args, **kwargs):
        evaluate(*args, **kwargs)
        return {"example.lookup0"}

    monkeypatch.setattr(runtime, "_evaluate", failing)
    payload = care(runtime)
    with pytest.raises(ValueError, match="catalog_evaluation_failed"):
        await runtime.submit("catalog_care_write", {"request": payload})
    assert runtime.state is before and runtime.state.tables == tables
    assert runtime.latches == latches and runtime.pending_latches is None
    assert runtime.pending_refresh == set()
    assert await runtime.store.get("catalog_request", payload["request_id"]) is None


@pytest.mark.postgres
async def test_postgres_readers_cannot_observe_half_of_a_care_transaction(
    database, config, monkeypatch
):
    store = PostgresStore(database, MIGRATIONS, tls=False)
    await store.open()
    runtime = Runtime(store, FakeClock(NOW), type_registry())
    await runtime.start()
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        catalog = await populated(runtime, config, large=False)
        original = store.put_metadata

        async def gated(key, value):
            if key == "runtime":
                # Already inside the SQL transaction, after all head/result writes.
                entered.set()
                await release.wait()
            await original(key, value)

        async def read_pair():
            assert store.pool is not None
            row = await store.pool.fetchrow(
                "SELECT (SELECT payload->>'revision' FROM catalog_head WHERE key=$1) AS revision, "
                "(SELECT payload->'fields'->'value'->>'value' FROM contract_state_current WHERE key=$2) AS value",
                head_key(catalog),
                "example.lookup1",
            )
            return int(row["revision"]), row["value"]

        with monkeypatch.context() as patch:
            patch.setattr(store, "put_metadata", gated)
            writing = asyncio.create_task(
                runtime.submit("catalog_care_write", {"request": care(runtime)})
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert await read_pair() == (1, "other")
                assert head(runtime.state, catalog)["revision"] == 1
                assert (
                    runtime.snapshot()["contracts"]["example.lookup1"]["fields"]["value"]["value"]
                    == "other"
                )
            finally:
                release.set()
                await writing
        assert await read_pair() == (2, "example")
        assert head(runtime.state, catalog)["revision"] == 2
    finally:
        release.set()
        await runtime.stop()
        await store.close()


def test_committed_care_copy_is_independent_but_reuses_store_owned_unchanged_subtrees():
    source = State()
    source.tables["catalog_head"]["example"] = {
        "entries": {"a": {"note": "old"}, "b": {"note": "unchanged"}},
        "revision": 1,
    }
    source.tables["source_observation_current"]["example"] = {"value_raw": [1, 2, 3]}
    stored = source.clone()
    fork = source.catalog_clone()
    old = source.tables["catalog_head"]["example"]
    fork.tables["catalog_head"]["example"] = {
        "entries": {**old["entries"], "a": {"note": "new"}},
        "revision": 2,
    }
    result = fork.committed_copy(stored)
    assert (
        result.tables["catalog_head"]["example"]["entries"]["b"]
        is stored.tables["catalog_head"]["example"]["entries"]["b"]
    )
    fork.tables["catalog_head"]["example"]["entries"]["a"]["note"] = "later"
    assert result.tables["catalog_head"]["example"]["entries"]["a"]["note"] == "new"
    source.tables["source_observation_current"]["example"]["value_raw"].append(4)
    assert result.tables["source_observation_current"]["example"]["value_raw"] == [1, 2, 3]
