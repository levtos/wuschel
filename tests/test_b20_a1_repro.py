"""b20 regressions from the synthetic A1 reproductions; no installation data."""

import copy
from uuid import uuid4

import aiohttp

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.extensions.catalog.catalog_care import candidate_key, queue
from core_contracts.extensions.catalog.managed_catalog import head_key, identity
from core_contracts.freshness_templates import upgrade_document
from core_contracts.model import canonical
from core_contracts.security import Tokens
from tests.conftest import activate
from tests.test_beta_media import configuration, observe
from tests.test_catalog_care import seeded
from tests.test_catalog_title_keys import entry
from tests.test_managed_catalog import configured


async def test_active_export_baseline(runtime, config, aiohttp_client, tmp_path):
    await activate(runtime, configured(config))
    api = API(runtime, Tokens(tmp_path), str(uuid4()), ingress_address="127.0.0.1")
    client = await aiohttp_client(api.application(ingress=True))
    response = await client.post(
        "/api/v1/portable/export",
        json={"expected_active_revision": runtime.state.active_revision},
    )
    body = await response.json()
    assert response.status == 200, body


async def test_active_export_restored_connectivity_origin(
    runtime, config, aiohttp_client, tmp_path
):
    historical = copy.deepcopy(config)
    historical.update(config_schema_version=2, fingerprint_version=2)
    historical["bindings"][0] = {
        "binding_id": "binding.a",
        "source_id": "source.a",
        "adapter": {"kind": "connectivity"},
    }
    historical["sources"][0].update(
        source_type="state",
        value_schema={
            "type": "enum",
            "required": True,
            "physical_state": False,
            "value_catalog": ["ready", "starting", "unavailable"],
        },
    )
    historical["dependencies"][0]["kind"] = "connectivity"
    await activate(runtime, upgrade_document(historical))
    # Model a previously committed/restored document, not a newly accepted edit.
    # MemoryStore only. This shape was observed via the read-only active API.
    runtime.state = runtime.state.clone()  # Own the synthetic corruption before editing.
    runtime.state.tables["registry_revision"][str(runtime.state.active_revision)]["config"][
        "sources"
    ][0]["value_origin"] = {"kind": "measured", "provider": "local"}
    # Seed both sides as an already persisted historical defect. An ordinary
    # commit must still reject edits to immutable registry history.
    key = str(runtime.state.active_revision)
    runtime.store.state.tables["registry_revision"][key] = copy.deepcopy(
        runtime.state.tables["registry_revision"][key]
    )
    runtime.store.state.clean()
    runtime.state.clean()
    api = API(runtime, Tokens(tmp_path), str(uuid4()), ingress_address="127.0.0.1")
    client = await aiohttp_client(api.application(ingress=True))
    response = await client.post(
        "/api/v1/portable/export", json={"expected_active_revision": runtime.state.active_revision}
    )
    body = await response.json()
    assert response.status == 200, body


async def test_split_source_updates_do_not_create_mixed_pair(runtime, config):
    document = configuration(config, "media.homepods_class")
    document["dependencies"].append(
        {"dependency_id": "dep.ha", "kind": "ha", "monitor": "internal"}
    )
    for name, attribute in [("artist", "media_artist"), ("title", "media_title")]:
        selected = next(s for s in document["sources"] if s["source_id"] == "source." + name)
        selected.pop("origin")
        selected["dependency_id"] = "dep.ha"
        binding = next(b for b in document["bindings"] if b["source_id"] == "source." + name)
        binding["adapter"] = {
            "kind": "ha_attribute",
            "ha_entity_id": "media_player.synthetic",
            "ha_attribute": attribute,
        }
    await activate(runtime, document)
    await observe(runtime, "applicable", True)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")

        async def changed(artist, title):
            now = runtime.clock.now_utc().isoformat()
            await adapter.event(
                {
                    "kind": "changed",
                    "entity_id": "media_player.synthetic",
                    "new_state": {
                        "state": "playing",
                        "attributes": {"media_artist": artist, "media_title": title},
                        "last_changed": now,
                        "last_updated": now,
                    },
                },
                runtime.config,
            )
            await runtime.submit("tick")

        await changed("Synthetic Artist A", "Synthetic Title A")
        runtime.clock.advance(10)
        await changed("Synthetic Artist A", "Synthetic Title B")
        runtime.clock.advance(3)
        await changed("Synthetic Artist B", "Synthetic Title B")
    sightings = list(runtime.state.tables["catalog_sighting"].values())
    mixed = [
        r for r in sightings if r["key"] == canonical(["Synthetic Artist A", "Synthetic Title B"])
    ]
    assert not mixed, [(r["key"], r["seen_count"]) for r in sightings]


def test_candidate_key_keeps_different_full_titles_separate():
    left = entry(canonical(["Synthetic Artist A", "Synthetic Shared Prefix Alpha"]))
    right = entry(canonical(["Synthetic Artist A", "Synthetic Shared Prefix Beta"]))
    assert candidate_key(left) != candidate_key(right)


async def test_queue_does_not_offer_foreign_title(runtime, config):
    catalog = await seeded(runtime, config, [])
    for title in ["Synthetic Shared Prefix Alpha", "Synthetic Shared Prefix Beta"]:
        row = entry(canonical(["Synthetic Artist A", title]))
        key = identity(row["scope"], row["media_type"], row["signal_type"], row["key"])
        runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + key] = {
            **row,
            "identity": key,
            "catalog": head_key(catalog),
            "first_seen": "2026-01-01T12:00:00Z",
            "last_seen": "2026-01-01T12:00:00Z",
            "seen_count": 1,
            "report": "synthetic",
        }
    assert queue(runtime.state, catalog, "", "all", {})["groups"] == []


async def test_separate_source_observations_produce_one_phantom(runtime, config):
    await activate(runtime, configuration(config, "media.homepods_class"))
    await observe(runtime, "applicable", True)
    await observe(runtime, "artist", "Synthetic Artist A")
    await observe(runtime, "title", "Synthetic Title A")
    runtime.clock.advance(10)
    await observe(runtime, "title", "Synthetic Title B")
    runtime.clock.advance(3)
    await observe(runtime, "artist", "Synthetic Artist B")
    mixed = [
        r
        for r in runtime.state.tables["catalog_sighting"].values()
        if r["key"] == canonical(["Synthetic Artist A", "Synthetic Title B"])
    ]
    assert not mixed, [r["seen_count"] for r in mixed]


async def test_historical_export_preview_requires_explicit_validated_correction(
    runtime, config, aiohttp_client, tmp_path
):
    import pytest

    await test_active_export_restored_connectivity_origin(runtime, config, aiohttp_client, tmp_path)
    original = copy.deepcopy(
        runtime.state.tables["registry_revision"][str(runtime.state.active_revision)]["config"]
    )
    package = await runtime.submit(
        "portable_export", {"expected_active_revision": runtime.state.active_revision}
    )
    request = {"package": package}
    preview = await runtime.submit("portable_preview", request)
    assert preview["can_stage"] is False
    assert preview["can_correct_origins"] is True
    assert preview["configuration_issues"][0]["code"] == "adapter_value_origin_unsupported"
    with pytest.raises(ValueError, match="import_unresolved_conflicts"):
        await runtime.submit(
            "portable_stage",
            {**request, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())},
        )
    request["remove_unsupported_origins"] = True
    corrected = await runtime.submit("portable_preview", request)
    assert corrected["unresolved"] == []
    assert "value_origin" not in corrected["config"]["sources"][0]
    draft = await runtime.submit(
        "portable_stage",
        {**request, "plan_digest": corrected["plan_digest"], "request_id": str(uuid4())},
    )
    assert "value_origin" not in draft["config"]["sources"][0]
    assert package["config"]["sources"][0]["value_origin"] == {
        "kind": "measured",
        "provider": "local",
    }
    assert (
        runtime.state.tables["registry_revision"][str(runtime.state.active_revision)]["config"]
        == original
    )


async def test_invalid_draft_export_still_requires_full_authoring_validation(
    runtime, config, aiohttp_client, tmp_path
):
    import pytest

    from core_contracts.validation import RegistryValidationError

    await test_active_export_restored_connectivity_origin(runtime, config, aiohttp_client, tmp_path)
    # Invalid drafts are deliberately modeled only inside the isolated MemoryStore.
    document = copy.deepcopy(
        runtime.state.tables["registry_revision"][str(runtime.state.active_revision)]["config"]
    )
    runtime.state.tables["registry_draft"]["synthetic.invalid"] = {
        "draft_id": "synthetic.invalid",
        "draft_version": 1,
        "config": document,
    }
    with pytest.raises(RegistryValidationError) as caught:
        await runtime.submit(
            "portable_export",
            {
                "draft_id": "synthetic.invalid",
                "draft_version": 1,
                "expected_active_revision": runtime.state.active_revision,
            },
        )
    assert any(issue.code == "adapter_value_origin_unsupported" for issue in caught.value.issues)
