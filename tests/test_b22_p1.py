"""P1 regressions, synthetic only; written now, first execution after all b22 packages."""

import copy
from uuid import uuid4

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.catalog_care import all_rows, listing
from core_contracts.freshness_templates import upgrade_document
from core_contracts.managed_catalog import head, head_key
from core_contracts.model import checksum
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens

from .conftest import activate, activation_body
from .test_beta_media import configuration, observe
from .test_catalog_care import care, seeded
from .test_declarative import definition, registry, schema
from .test_title_sync import observed


async def test_legacy_mqtt_origin_can_be_corrected_in_draft_without_changing_active(
    runtime, config, aiohttp_client, tmp_path
):
    data = definition()
    shape = schema("enum", value_catalog=["ready", "starting", "unavailable"])
    data["inputs"][0]["schema"] = shape
    data["schema"]["fields"]["value"] = shape
    document = registry(config, data)
    document["dependencies"][0]["kind"] = "mqtt"
    document["bindings"][0].update(adapter={"kind": "connectivity"}, transform=None)
    await activate(runtime, upgrade_document(document))

    # Seed a genuinely historical document in the synthetic MemoryStore. New
    # authoring still rejects it; no production migration relaxes validation.
    revision = str(runtime.state.active_revision)
    historical = copy.deepcopy(runtime.state.tables["registry_revision"][revision])
    historical["config"]["sources"][0]["value_origin"] = {"kind": "measured", "provider": "local"}
    historical["checksum"] = checksum(historical["config"])
    runtime.state.tables["registry_revision"][revision] = copy.deepcopy(historical)
    runtime.store.state.tables["registry_revision"][revision] = copy.deepcopy(historical)
    await runtime.submit("mqtt_state", "connected")
    before = runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]
    assert before["status"] == "unknown"
    assert any(r["code"] == "restore_context_incompatible" for r in before["reasons"])

    client = await aiohttp_client(
        API(runtime, Tokens(tmp_path), str(uuid4()), ingress_address="127.0.0.1").application(
            ingress=True
        )
    )
    headers = {"X-Authoring-Protocol": "2"}
    response = await client.post("/api/v1/registry/freshness", json=historical["config"])
    assert response.status == 400
    assert any(
        i["code"] == "adapter_value_origin_unsupported" for i in (await response.json())["issues"]
    )
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={
            "config": historical["config"],
            "expected_active_revision": int(revision),
        },
    )
    assert response.status == 200
    draft = await response.json()
    path = "/api/v1/registry/drafts/" + draft["draft_id"]
    assert (await client.get(path)).status == 200
    assert draft["config"]["sources"][0]["value_origin"]
    corrected = copy.deepcopy(draft["config"])
    corrected["sources"][0].pop("value_origin")
    response = await client.put(
        path,
        headers=headers,
        json={
            "draft_version": draft["draft_version"],
            "config": corrected,
        },
    )
    assert response.status == 200
    saved = await response.json()
    assert (await client.post("/api/v1/registry/freshness", json=saved["config"])).status == 200
    assert (await client.post(path + "/validate", headers=headers, json={})).status == 200
    assert runtime.state.active_revision == int(revision)
    assert runtime.state.tables["registry_revision"][revision] == historical
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"] == "unknown"
    response = await client.post(
        path + "/activate",
        headers=headers,
        json=await activation_body(client, path, headers, saved["draft_version"]),
    )
    assert response.status == 200
    # No new mqtt_state or reconnect is needed after explicit activation.
    field = runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]
    assert (
        field["status"] == "valid" and field["value"] == "ready" and field["quality"] == "healthy"
    )
    assert runtime.state.tables["registry_revision"][revision] == historical


@pytest.mark.parametrize("alias", [False, True])
async def test_imported_row_sorts_by_local_playback_including_alias(runtime, config, alias):
    rows = [observed("Synthetic Older"), observed("Synthetic Recent")]
    rows[0]["telemetry"].update(first_seen="2020-01-01T00:00:00Z", last_seen="2020-01-02T00:00:00Z")
    rows[1]["telemetry"].update(first_seen="2020-02-01T00:00:00Z", last_seen="2020-02-02T00:00:00Z")
    catalog = await seeded(runtime, config, rows)
    initial = listing(runtime.state, catalog, sort="newest")["items"]
    assert initial[0]["tc_metadata"]["title"] == "Synthetic Recent"
    entries = head(runtime.state, catalog)["entries"]
    selected = next(
        (key, row)
        for key, row in entries.items()
        if row["tc_metadata"]["title"] == "Synthetic Older"
        and bool(row["tc_metadata"].get("is_alias")) == alias
    )
    key, row = selected
    runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + key] = {
        **{field: row[field] for field in ("scope", "media_type", "signal_type", "key")},
        "catalog": head_key(catalog),
        "identity": key,
        "first_seen": "2026-01-01T12:00:00+00:00",
        "last_seen": "2026-01-01T12:00:00+00:00",
        "seen_count": 1,
    }
    actual = listing(runtime.state, catalog, sort="newest")["items"]
    assert len(actual) == 2  # An alias never becomes another visible title.
    assert actual[0]["tc_metadata"]["title"] == "Synthetic Older"
    assert actual[0]["last_seen"] == "2026-01-01T12:00:00+00:00"
    assert actual[0]["tc_metadata"]["telemetry"]["last_seen"] == "2020-01-02T00:00:00Z"
    assert actual[0]["local_sightings"]["seen_count"] == 1
    assert actual[1]["last_seen"] == "2020-02-02T00:00:00Z"
    assert (
        listing(runtime.state, catalog, sort="oldest")["items"][0]["identity"]
        == actual[1]["identity"]
    )


async def test_app_names_survive_switch_late_name_and_restart_without_extra_playback(
    runtime, config
):
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
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": catalog.catalog_id,
                "definition_version": catalog.version,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": 0,
                "request_id": str(uuid4()),
                "reason": "Synthetic existing imported display",
                "changes": [
                    {
                        "scope": "appletv",
                        "media_type": "video",
                        "signal_type": "app",
                        "key": "synthetic.app.a",
                        "display_name": "synthetic.app.a",
                        "classification": catalog.classification_schema.values[0],
                    }
                ],
            }
        },
    )
    original_head = copy.deepcopy(head(runtime.state, catalog))
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
        selected = next(b for b in runtime.config.bindings if b.source_id == "source.title")

        async def emit(key, name, state="playing"):
            runtime.clock.advance(0.01)
            now = runtime.clock.now_utc().isoformat()
            item = adapter.observation(
                selected,
                {
                    "state": state,
                    "attributes": {"app_id": key, "app_name": name},
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

        await emit("synthetic.app.a", None)
        await emit("synthetic.app.a", "Synthetic App A", "paused")
        await emit("synthetic.app.b", "Synthetic App B")
        await emit("synthetic.app.b", "", "paused")
        await emit("", "Synthetic Empty", "idle")
    items = {row["key"]: row for row in all_rows(runtime.state, catalog).values()}
    assert set(items) == {"synthetic.app.a", "synthetic.app.b"}
    assert {key: row["display_name"] for key, row in items.items()} == {
        "synthetic.app.a": "Synthetic App A",
        "synthetic.app.b": "Synthetic App B",
    }
    assert sum(row["seen_count"] for row in items.values()) == 2
    assert head(runtime.state, catalog) == original_head
    assert {row["display_name"] for row in runtime.state.tables["catalog_sighting"].values()} == {
        "Synthetic App A",
        "Synthetic App B",
    }
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        after = {row["key"]: row for row in all_rows(restored.state, catalog).values()}
        assert {key: row["display_name"] for key, row in after.items()} == {
            key: row["display_name"] for key, row in items.items()
        }
        assert sum(row["seen_count"] for row in after.values()) == 2
    finally:
        await restored.stop()


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "catalog_master_missing"),
        ("not_selected", "catalog_master_not_selected"),
        ("scope", "catalog_master_scope_mismatch"),
        ("variant", "catalog_master_is_variant"),
        ("children", "catalog_master_has_variants"),
    ],
)
async def test_grouping_explains_each_invalid_master_and_keeps_state(
    runtime, config, aiohttp_client, tmp_path, case, code
):
    catalog = await seeded(
        runtime,
        config,
        [observed("Synthetic One"), observed("Synthetic Two"), observed("Synthetic Three")],
    )
    ids = list(all_rows(runtime.state, catalog))
    if case in {"variant", "children"}:
        await runtime.submit(
            "catalog_care_write", {"request": care(runtime, ids[:2], "group", master=ids[0])}
        )
    if case == "scope":
        # Separate signal kinds are legitimate entries, but cannot be grouped.
        entry = head(runtime.state, catalog)["entries"][ids[1]]
        await runtime.submit(
            "catalog_write",
            {
                "request": {
                    "catalog_id": catalog.catalog_id,
                    "definition_version": catalog.version,
                    "expected_active_revision": runtime.state.active_revision,
                    "expected_content_revision": head(runtime.state, catalog)["revision"],
                    "request_id": str(uuid4()),
                    "reason": "Synthetic different scope",
                    "changes": [
                        {
                            "scope": "other",
                            "media_type": "music",
                            "signal_type": "title",
                            "key": entry["key"],
                            "classification": "normal",
                        }
                    ],
                }
            },
        )
        ids[1] = next(
            key for key, row in all_rows(runtime.state, catalog).items() if row["scope"] == "other"
        )
    master = (
        None
        if case == "missing"
        else "absent"
        if case == "not_selected"
        else ids[1]
        if case == "variant"
        else ids[2]
        if case == "children"
        else ids[0]
    )
    payload = care(runtime, ids, "group", master=master)
    original = runtime.state.clone()
    client = await aiohttp_client(
        API(runtime, Tokens(tmp_path), str(uuid4()), ingress_address="127.0.0.1").application(
            ingress=True
        )
    )
    for path in ("preview", "write"):
        response = await client.post("/api/v1/catalogs/care/" + path, json=payload)
        assert response.status == 400
        assert (await response.json())["error"] == code
        assert runtime.state.tables == original.tables
