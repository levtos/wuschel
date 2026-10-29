import copy
from uuid import uuid4

import pytest

from core_contracts.managed_catalog import head
from core_contracts.model import canonical
from core_contracts.runtime import Runtime
from core_contracts.title_sync import current_mapping

from .conftest import activate
from .test_beta_media_import import document, row

ACTOR = {"kind": "token", "role": "admin"}


async def inspect(runtime, rows, mapping=None):
    body = {
        "expected_active_revision": runtime.state.active_revision,
        "_snapshot": {"version": 3, "entries": rows},
        "_actor": ACTOR,
    }
    if mapping is not None:
        body.update(
            mapping=mapping, expected_mapping_version=current_mapping(runtime.state)["version"]
        )
    return await runtime.submit("title_sync_preview", body)


async def apply(runtime, plan, request_id=None):
    return await runtime.submit(
        "title_sync_apply",
        {
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "request_id": request_id or str(uuid4()),
            "_actor": ACTOR,
        },
    )


def mapping():
    return {
        "mappings": {
            canonical(["synthetic", "synthetic"]): {
                "catalog_id": "example.titles",
                "definition_version": 1,
                "scope": "synthetic",
                "profile": "music",
            }
        },
        "standalone": [],
    }


def observed(name="Synthetic First", **updates):
    return {
        **row(name),
        "id": str(uuid4()),
        "artist": "Synthetic Artist - With Delimiter",
        "title": name,
        "telemetry": {
            "first_seen": "2032-01-01T00:00:00Z",
            "last_seen": "2032-01-02T00:00:00Z",
            "seen_count": 12,
        },
        **updates,
    }


async def test_saved_mapping_reused_after_restart_second_sync_is_noop(runtime, config):
    await activate(runtime, document(config))
    rows = [observed(hidden_at="2032-01-01T00:00:00Z", reviewed_at="2032-01-01T00:00:00Z", enum=0)]
    plan = await inspect(runtime, rows, mapping())
    assert plan["can_apply"], plan["errors"]
    assert plan["counts"] == {"new": 1}
    assert not runtime.state.tables["title_sync_mapping"]
    first = await apply(runtime, plan)
    assert first["changed"] == 2 and first["mapping_version"] == 1
    assert await apply(runtime, plan, first["request_id"]) == first
    assert head(runtime.state, runtime.config.catalogs[0])["revision"] == 1
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        second_plan = await inspect(restored, rows)
        assert second_plan["counts"] == {"unchanged": 1}
        second = await apply(restored, second_plan)
        assert second["changed"] == 0
        assert len(await restored.store.rows("catalog_content_revision")) == 1
        entries = head(restored.state, restored.config.catalogs[0])["entries"]
        assert all(
            item["tc_metadata"]["telemetry"]["seen_count"] == 12 for item in entries.values()
        )
        assert all(item["classification"] in (None, "normal") for item in entries.values())
    finally:
        await restored.stop()


async def test_tc_deltas_replace_and_local_only_rows_remain(runtime, config):
    await activate(runtime, document(config))
    rows = [observed()]
    first = await inspect(runtime, rows, mapping())
    await apply(runtime, first)
    original = copy.deepcopy(head(runtime.state, runtime.config.catalogs[0]))
    rows[0]["telemetry"]["seen_count"] = 13
    rows[0]["enum"] = 2
    rows[0]["hidden_at"] = "2032-01-03T00:00:00Z"
    changed = await inspect(runtime, rows)
    assert changed["counts"] == {"changed": 1}
    await apply(runtime, changed)
    current = head(runtime.state, runtime.config.catalogs[0])
    assert current["revision"] == 2
    assert all(
        item["tc_metadata"]["telemetry"]["seen_count"] == 13 for item in current["entries"].values()
    )
    assert {item["classification"] for item in current["entries"].values()} == {None, "stumm"}
    # A snapshot which no longer mentions an entry does not delete that entry.
    empty = await inspect(runtime, [])
    await apply(runtime, empty)
    assert head(runtime.state, runtime.config.catalogs[0]) == current
    assert original["entries"].keys() == current["entries"].keys()


async def test_preview_is_immutable_and_target_conflict_blocks_entire_operation(runtime, config):
    await activate(runtime, document(config))
    rows = [observed()]
    plan = await inspect(runtime, rows, mapping())
    rows[0]["enum"] = 2
    await apply(runtime, plan)
    assert (
        next(
            item
            for item in head(runtime.state, runtime.config.catalogs[0])["entries"].values()
            if item["classification"]
        )["classification"]
        == "boost"
    )
    plan = await inspect(runtime, rows)
    await apply(runtime, plan)
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await apply(runtime, plan)
    unknown = await inspect(runtime, [{**observed(), "contexts": [{"context": "other"}]}])
    assert not unknown["can_apply"]
    with pytest.raises(ValueError, match="title_sync_mapping_invalid"):
        await apply(runtime, unknown)


async def test_hidden_parent_closure_override_conflict_and_saved_standalone(runtime, config):
    from core_contracts.title_sync import standalone_key
    from core_contracts.title_transfer import TargetMapping

    await activate(runtime, document(config))
    master = observed("Synthetic Master", contexts=[], hidden_at="2032-01-01T00:00:00Z", enum=0)
    child = observed(
        "Synthetic Variant", parent_key=master["normalized_key"], parent_id=master["id"], enum=2
    )
    rows = [master, child]
    plan = await inspect(runtime, rows, mapping())
    assert plan["can_apply"], plan["errors"]
    await apply(runtime, plan)
    items = head(runtime.state, runtime.config.catalogs[0])["entries"].values()
    assert any(item.get("hidden_at") for item in items)
    assert all(item["classification"] in (None, "normal") for item in items)
    child["contexts"][0]["enum_override"] = 2
    conflict = await inspect(runtime, rows)
    assert not conflict["can_apply"]
    assert any("title_sync_variant_override" in error["reasons"] for error in conflict["errors"])
    changed_mapping = mapping()
    target = TargetMapping.model_validate(next(iter(changed_mapping["mappings"].values())))
    changed_mapping["standalone"] = [standalone_key(child, target)]
    explicit = await inspect(runtime, rows, changed_mapping)
    await apply(runtime, explicit)
    assert current_mapping(runtime.state)["version"] == 2
    assert (await inspect(runtime, rows))["can_apply"]


async def test_multi_catalog_atomic_failure_and_tc_reassertion(runtime, config, monkeypatch):
    from core_contracts.managed_catalog import identity
    from core_contracts.persistence import PersistenceUnavailable

    doc = document(config)
    second = copy.deepcopy(doc["catalogs"][0])
    second["catalog_id"] = "example.second"
    doc["catalogs"].append(second)
    await activate(runtime, doc)
    mappings = mapping()
    mappings["mappings"][canonical(["second", ""])] = {
        **next(iter(mappings["mappings"].values())),
        "catalog_id": "example.second",
    }
    row = observed()
    row["contexts"].append({"context": "second", "source_app": ""})
    plan = await inspect(runtime, [row], mappings)
    before = copy.deepcopy(runtime.state.tables)
    original_commit = runtime.store.commit

    async def fail(_state):
        raise PersistenceUnavailable("synthetic failure")

    monkeypatch.setattr(runtime.store, "commit", fail)
    with pytest.raises(PersistenceUnavailable):
        await apply(runtime, plan)
    assert runtime.state.tables == before
    monkeypatch.setattr(runtime.store, "commit", original_commit)
    await runtime.submit("recover", {})
    await apply(runtime, plan)
    assert [head(runtime.state, c)["revision"] for c in runtime.config.catalogs] == [1, 1]
    primary = canonical([row["artist"], row["title"]])
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": "example.titles",
                "definition_version": 1,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": 1,
                "request_id": str(uuid4()),
                "reason": "Synthetic local decision",
                "changes": [
                    {
                        "scope": "synthetic",
                        "media_type": "music",
                        "signal_type": "title",
                        "key": primary,
                        "classification": "stumm",
                        "note": "Synthetic local note",
                    }
                ],
            }
        },
    )
    fresh = await inspect(runtime, [row])
    assert fresh["counts"] == {"changed": 1}
    await apply(runtime, fresh)
    stored = head(runtime.state, runtime.config.catalogs[0])["entries"][
        identity("synthetic", "music", "title", primary)
    ]
    assert stored["classification"] == "boost"
    assert stored["note"] == "Synthetic local note"
    assert stored["tc_metadata"]["telemetry"]["seen_count"] == 12


async def test_empty_and_invalid_records_do_not_crash_and_diff_is_paged(runtime, config):
    from core_contracts.title_sync import diff_page

    await activate(runtime, document(config))
    invalid = await inspect(runtime, [{"key": "Synthetic incomplete"}], mapping())
    assert not invalid["can_apply"] and invalid["counts"] == {"unmappable": 1}
    rows = [observed(f"Synthetic {n}") for n in range(60)]
    plan = await inspect(runtime, rows, mapping())
    first = diff_page(runtime, ACTOR, plan["plan_id"])
    second = diff_page(runtime, ACTOR, plan["plan_id"], first["next_cursor"])
    assert len(first["items"]) == 100 and len(second["items"]) == 20
    assert not second["next_cursor"]
    assert not ({r["cursor"] for r in first["items"]} & {r["cursor"] for r in second["items"]})


async def test_sync_api_requires_admin_sanitizes_errors_and_hides_internal_plan(
    runtime, config, aiohttp_client, tmp_path
):
    from .test_workbench_api import api_client

    await activate(runtime, document(config))
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    denied = await client.post("/api/v1/title-sync/preview", headers=consumer, json={})
    assert denied.status == 403
    missing = await client.post("/api/v1/title-sync/preview", headers=headers, json={})
    assert (await missing.json())["error"] == "title_sync_not_configured"

    async def reader():
        return {"version": 3, "entries": [observed()]}

    api.title_reader = reader
    response = await client.post(
        "/api/v1/title-sync/preview",
        headers=headers,
        json={
            "expected_active_revision": runtime.state.active_revision,
            "mapping": mapping(),
            "expected_mapping_version": 0,
        },
    )
    assert response.status == 200
    result = await response.json()
    assert result["can_apply"] and "groups" not in result
    diff = await client.get(
        "/api/v1/title-sync/diff", headers=headers, params={"plan_id": result["plan_id"]}
    )
    assert len((await diff.json())["items"]) == 2
    assert not runtime.state.tables["title_sync_mapping"]
