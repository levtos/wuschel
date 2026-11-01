"""b21: synthetic proofs of the confirmed, atomic scope repair."""

import copy
from uuid import uuid4

import pytest

from core_contracts.catalog_care import all_rows, listing
from core_contracts.managed_catalog import effective, head, head_key, identity
from core_contracts.model import canonical

from .conftest import activate
from .test_beta_media import configuration, field, observe
from .test_title_sync import apply, inspect, observed


def entry(key="Synthetic Title", scope="default", **extra):
    return {
        "scope": scope,
        "media_type": "music",
        "signal_type": "title",
        "key": key,
        "classification": "boost",
        **extra,
    }


async def write(runtime, catalog, rows):
    return await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": catalog.catalog_id,
                "definition_version": catalog.version,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": head(runtime.state, catalog)["revision"],
                "request_id": str(uuid4()),
                "reason": "Synthetic fixture",
                "changes": rows,
            }
        },
    )


async def preview(runtime, clear_pc=True):
    return await runtime.submit(
        "catalog_scope_preview",
        {"expected_active_revision": runtime.state.active_revision, "clear_pc": clear_pc},
    )


async def repair(runtime, plan=None, **extra):
    plan = plan or await preview(runtime)
    return await runtime.submit(
        "catalog_scope_apply",
        {
            "expected_active_revision": runtime.state.active_revision,
            "clear_pc": plan["clear_pc"],
            "request_id": str(uuid4()),
            "plan_digest": plan["plan_digest"],
            "confirmed": True,
            **extra,
        },
    )


async def setup(runtime, config, type_id="media.homepods_class"):
    await activate(runtime, configuration(config, type_id))
    key = "media." + {"media.homepods_class": "homepods", "media.pc_class": "pc"}[type_id]
    return next(c for c in runtime.config.catalogs if c.catalog_id == key)


async def test_imported_boost_is_looked_up_in_device_scope_and_preview_is_readonly(runtime, config):
    catalog = await setup(runtime, config)
    key = canonical(["Synthetic Artist", "Synthetic Title"])
    await write(runtime, catalog, [entry(key)])
    await observe(runtime, "applicable", True)
    await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Title")
    assert field(runtime)["value"] == "normal"
    before = runtime.state.model_dump()
    plan = await preview(runtime)
    assert runtime.state.model_dump() == before
    assert plan["catalogs"][0]["moved"] == 1
    await repair(runtime, plan)
    assert field(runtime)["value"] == "boost"
    assert (
        field(runtime)["measured_at"]
        == before["tables"]["contract_state_current"]["fixture.media"]["fields"]["value"][
            "measured_at"
        ]
    )
    assert head(runtime.state, catalog)["entries"][identity("default", "music", "title", key)][
        "tombstone"
    ]


@pytest.mark.parametrize("live_class,expected", [("normal", "normal"), (None, "boost")])
async def test_collision_preserves_class_metadata_and_merges_sightings(
    runtime, config, live_class, expected
):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry(
                display_name="Synthetic display",
                note="Synthetic import note",
                reviewed_at="2032-01-01T00:00:00Z",
                hidden_at="2032-01-02T00:00:00Z",
            ),
            entry(scope="homepods", classification=live_class),
        ],
    )
    ck = head_key(catalog)
    for scope, count, first, last in [
        ("default", 3, "2032-01-01", "2032-01-03"),
        ("homepods", 5, "2032-01-02", "2032-01-04"),
    ]:
        ident = identity(scope, "music", "title", "Synthetic Title")
        runtime.state.tables["catalog_sighting"][ck + ":" + ident] = {
            "catalog": ck,
            "identity": ident,
            **entry(scope=scope),
            "seen_count": count,
            "first_seen": first + "T00:00:00Z",
            "last_seen": last + "T00:00:00Z",
        }
    await repair(runtime)
    rows = all_rows(runtime.state, catalog)
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["effective_classification"] == expected
    assert row["display_name"] == "Synthetic display" and row["note"] == "Synthetic import note"
    assert row["hidden_at"] and row["reviewed_at"]
    assert row["seen_count"] == 8
    assert (
        row["first_seen"] == "2032-01-01T00:00:00Z" and row["last_seen"] == "2032-01-04T00:00:00Z"
    )


async def test_master_variants_move_together_and_repeated_action_returns_receipt(runtime, config):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master"),
            entry("Synthetic Variant", classification=None, master_key="Synthetic Master"),
        ],
    )
    history = copy.deepcopy(await runtime.store.rows("catalog_entry_revision"))
    result = await repair(runtime)
    rows = all_rows(runtime.state, catalog)
    assert len(rows) == 2 and {r["scope"] for r in rows.values()} == {"homepods"}
    child = rows[identity("homepods", "music", "title", "Synthetic Variant")]
    assert child["master_identity"] == identity("homepods", "music", "title", "Synthetic Master")
    assert child["effective_classification"] == "boost"
    before = runtime.state.model_dump()
    assert await repair(runtime) == result
    assert runtime.state.model_dump() == before
    saved_history = await runtime.store.rows("catalog_entry_revision")
    assert all(row in saved_history for row in history)
    runtime.state = await runtime.store.load()
    assert await preview(runtime) == result


async def test_import_provenance_and_telemetry_survive_collision_and_reset_epoch(runtime, config):
    from core_contracts.title_sync import metadata

    catalog = await setup(runtime, config)
    original = metadata(observed()).model_dump(mode="json")
    await write(
        runtime,
        catalog,
        [entry(tc_metadata=original), entry(scope="homepods", classification="normal")],
    )
    ident = identity("homepods", "music", "title", "Synthetic Title")
    runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + ident] = {
        "catalog": head_key(catalog),
        "identity": ident,
        **entry(scope="homepods"),
        "seen_count": 5,
        "first_seen": "2032-01-02T00:00:00Z",
        "last_seen": "2032-01-04T00:00:00Z",
    }
    await repair(runtime)
    row = all_rows(runtime.state, catalog)[ident]
    assert row["tc_metadata"]["source_identity"] == original["source_identity"]
    assert row["tc_metadata"]["contexts"] == original["contexts"]
    assert row["seen_count"] == 17 and row["first_seen"] == original["telemetry"]["first_seen"]
    runtime.state.tables["service_state"]["catalog_counter_epoch"] = {"at": "2032-01-03T00:00:00Z"}
    assert all_rows(runtime.state, catalog)[ident]["seen_count"] == 5


async def test_variant_collision_keeps_reference_and_live_class(runtime, config):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master"),
            entry("Synthetic Variant", classification=None, master_key="Synthetic Master"),
            entry("Synthetic Variant", scope="homepods", classification="normal"),
        ],
    )
    await repair(runtime)
    rows = all_rows(runtime.state, catalog)
    child = rows[identity("homepods", "music", "title", "Synthetic Variant")]
    assert child["master_key"] == "Synthetic Master"
    assert child["effective_classification"] == "normal"


async def test_pc_clear_tombstones_all_entries_and_a_new_playback_appears(runtime, config):
    catalog = await setup(runtime, config, "media.pc_class")
    await write(runtime, catalog, [entry(scope="pc", media_type="game", classification="headset")])
    await observe(runtime, "applicable", True)
    await observe(runtime, "title", "Synthetic Title")
    runtime.clock.advance(2)
    await runtime.submit("catalog_tick")
    plan = await preview(runtime)
    assert (
        next(c for c in plan["catalogs"] if c["catalog_id"] == catalog.catalog_id)["removed"] == 1
    )
    await repair(runtime, plan)
    assert all_rows(runtime.state, catalog) == {}
    await observe(runtime, "applicable", False)
    await observe(runtime, "applicable", True)
    await observe(runtime, "title", "Synthetic Title")
    runtime.clock.advance(2)
    await runtime.submit("catalog_tick")
    rows = all_rows(runtime.state, catalog)
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["classification"] is None and row["seen_count"] == 1


async def test_pc_clear_can_be_disabled(runtime, config):
    catalog = await setup(runtime, config, "media.pc_class")
    await write(runtime, catalog, [entry(media_type="game", classification="headset")])
    await repair(runtime, await preview(runtime, False))
    assert next(iter(all_rows(runtime.state, catalog).values()))["scope"] == "pc"


async def test_pc_clear_also_removes_sightings_of_already_tombstoned_entries(runtime, config):
    catalog = await setup(runtime, config, "media.pc_class")
    await write(
        runtime,
        catalog,
        [entry(media_type="game", scope="pc", classification="headset", tombstone=True)],
    )
    ident = identity("pc", "game", "title", "Synthetic Title")
    runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + ident] = {
        "catalog": head_key(catalog),
        "identity": ident,
        **entry(media_type="game", scope="pc"),
        "seen_count": 1,
        "first_seen": "2032-01-01T00:00:00Z",
        "last_seen": "2032-01-01T00:00:00Z",
    }
    await repair(runtime)
    assert not any(
        row["catalog"] == head_key(catalog)
        for row in runtime.state.tables["catalog_sighting"].values()
    )


async def test_sync_uses_saved_device_scope_and_rejects_explicit_default(runtime, config):
    catalog = await setup(runtime, config)
    source_key = canonical(["synthetic", "synthetic"])
    target = {
        "catalog_id": catalog.catalog_id,
        "definition_version": 1,
        "scope": "default",
        "profile": "music",
    }
    runtime.state.tables["title_sync_mapping"]["1"] = {
        "version": 1,
        "mapping": {"mappings": {source_key: target}, "standalone": []},
    }
    plan = await inspect(runtime, [observed()])
    assert plan["mapping"]["mappings"][source_key]["scope"] == "homepods"
    await apply(runtime, plan)
    assert {r["scope"] for r in head(runtime.state, catalog)["entries"].values()} == {"homepods"}
    with pytest.raises(ValueError, match="title_sync_mapping_invalid"):
        await inspect(runtime, [observed()], {"mappings": {source_key: target}, "standalone": []})


async def test_failed_commit_and_stale_preview_leave_everything_unchanged(runtime, config):
    catalog = await setup(runtime, config)
    await write(runtime, catalog, [entry()])
    plan = await preview(runtime)
    await write(runtime, catalog, [entry("Synthetic Other")])
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await repair(runtime, plan)
    assert runtime.state.model_dump() == before
    plan = await preview(runtime)

    def fail():
        raise RuntimeError("synthetic commit failure")

    runtime.store.before_commit = fail
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await repair(runtime, plan)
    runtime.store.before_commit = None
    assert runtime.state.model_dump() == before


async def test_local_class_without_review_stays_open(runtime, config):
    catalog = await setup(runtime, config)
    await write(runtime, catalog, [entry(scope="homepods")])
    rows = listing(runtime.state, catalog, view="open")["items"]
    assert len(rows) == 1 and rows[0]["complete"] and rows[0]["review_status"] == "open"


async def test_apple_tv_uses_app_name_with_unchanged_key_and_ignores_empty_keys(runtime, config):
    import aiohttp

    from core_contracts.adapters.ha import BridgeAdapter

    document = configuration(config, "media.appletv_class")
    document["dependencies"].append(
        {"dependency_id": "dep.ha", "kind": "ha", "monitor": "internal"}
    )
    source = next(s for s in document["sources"] if s["source_id"] == "source.title")
    source.pop("origin")
    source["dependency_id"] = "dep.ha"
    binding = next(b for b in document["bindings"] if b["source_id"] == "source.title")
    binding["adapter"] = {
        "kind": "ha_attribute",
        "ha_entity_id": "media_player.synthetic",
        "ha_attribute": "app_id",
    }
    await activate(runtime, document)
    await runtime.submit("bridge_state", ("connected", "connected"))
    runtime.first_snapshot = True
    await observe(runtime, "applicable", True)
    catalog = next(c for c in runtime.config.catalogs if c.catalog_id == "media.appletv")
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
        selected = next(b for b in runtime.config.bindings if b.source_id == "source.title")

        async def emit(key):
            runtime.clock.advance(0.01)
            now = runtime.clock.now_utc().isoformat()
            item = adapter.observation(
                selected,
                {
                    "state": "playing",
                    "attributes": {"app_id": key, "app_name": "Synthetic App"},
                    "last_changed": now,
                    "last_updated": now,
                },
                "live_change",
                {},
                runtime.config,
            )
            await runtime.submit("observation", item)
            runtime.clock.advance(2)
            await runtime.submit("catalog_tick")

        await emit("synthetic.application")
        rows = all_rows(runtime.state, catalog)
        assert len(rows) == 1
        assert next(iter(rows.values()))["display_name"] == "Synthetic App"
        assert next(iter(rows.values()))["key"] == "synthetic.application"
        await emit("")
        assert len(all_rows(runtime.state, catalog)) == 1
    empty = identity("appletv", "video", "app", "")
    runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + empty] = {
        "catalog": head_key(catalog),
        "identity": empty,
        "scope": "appletv",
        "media_type": "video",
        "signal_type": "app",
        "key": "",
        "first_seen": runtime.clock.now_utc().isoformat(),
        "last_seen": runtime.clock.now_utc().isoformat(),
        "seen_count": 1,
    }
    await repair(runtime)
    assert len(all_rows(runtime.state, catalog)) == 1
    assert all(row["key"] for row in all_rows(runtime.state, catalog).values())


async def test_scope_repair_keeps_unaffected_timer_basis_and_machine_state(runtime, config):
    from .test_declarative import literal, machine_definition, registry, schema
    from .test_managed_catalog import change, configured, lookup_rule, request

    machine = machine_definition()
    machine["language_version"] = 4
    machine["outputs"]["classification"] = lookup_rule("Synthetic Unaffected")
    machine["schema"]["fields"]["classification"] = schema(
        "enum", value_catalog=["other", "example"]
    )
    document = configured(registry(config, machine))
    rule = {
        "op": "temporal",
        "id": "timer",
        "operation": "stable_for",
        "duration_s": 60,
        "accepts_held": False,
        "args": [
            {
                "op": "compare",
                "comparison": "eq",
                "left": lookup_rule("Synthetic Unaffected"),
                "right": {
                    **literal("other", "enum"),
                    "schema": schema("enum", value_catalog=["other", "example"]),
                },
            }
        ],
    }
    document["definitions"].append(
        {
            "type_id": "example.timer",
            "type_version": 1,
            "language_version": 4,
            "inputs": [],
            "schema": {"fields": {"value": schema()}, "available_projection": ["value"]},
            "outputs": {"value": rule},
        }
    )
    document["contracts"].append(
        {
            "contract_id": "example.timer",
            "type_id": "example.timer",
            "type_version": 1,
            "enabled": True,
            "parameters": {},
            "inputs": [],
        }
    )
    await activate(runtime, document)
    await runtime.submit(
        "catalog_write",
        {"request": request(runtime, [change("Synthetic Changed", scope="default")])},
    )
    before_nodes = copy.deepcopy(runtime.state.tables["node_state"])
    before_states = copy.deepcopy(runtime.state.tables["contract_state_current"])
    runtime.clock.advance(10)
    await repair(runtime)
    assert runtime.state.tables["node_state"] == before_nodes
    assert runtime.state.tables["contract_state_current"] == before_states
    catalog = runtime.config.catalogs[0]
    assert (
        effective(
            head(runtime.state, catalog)["entries"],
            identity("example", "music", "title", "Synthetic Changed"),
            catalog,
        )[0]
        == "example"
    )


async def test_scope_api_requires_confirmation_and_binds_revision(
    runtime, config, aiohttp_client, tmp_path
):
    from .test_workbench_api import api_client

    catalog = await setup(runtime, config)
    await write(runtime, catalog, [entry()])
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    body = {"expected_active_revision": runtime.state.active_revision, "clear_pc": True}
    response = await client.post("/api/v1/catalogs/scope/preview", json=body, headers=headers)
    assert response.status == 200
    plan = await response.json()
    response = await client.post(
        "/api/v1/catalogs/scope/apply",
        json={**body, "plan_digest": plan["plan_digest"], "request_id": str(uuid4())},
        headers=headers,
    )
    assert response.status == 409
    assert not (await preview(runtime))["complete"]
    response = await client.post(
        "/api/v1/catalogs/scope/apply",
        json={
            **body,
            "plan_digest": plan["plan_digest"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
        headers=headers,
    )
    assert response.status == 200 and (await response.json())["complete"]
