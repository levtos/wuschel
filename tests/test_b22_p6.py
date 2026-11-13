"""Synthetic P6 slot probes; execution deferred until the final b22 package."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.adapters import AdapterConfig
from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.extensions.catalog.catalog_care import all_rows, detail, listing
from core_contracts.extensions.catalog.interface_upgrade import upgrade_package
from core_contracts.extensions.catalog.managed_catalog import head, validate_heads
from core_contracts.freshness_templates import upgrade_document
from core_contracts.model import checksum
from core_contracts.portable import read_package
from core_contracts.stash_package import augment
from core_contracts.validation import validate_document as validate
from dev.build_stash_templates import build

from .conftest import activate
from .test_beta_media import configuration as old_configuration
from .test_catalog_playback import advance
from .test_portable import export

ROOT = Path(__file__).parents[1]
CATALOG = next(
    row
    for row in json.loads(
        (ROOT / "examples/interface-catalogs-b22.json").read_text(encoding="utf-8")
    )
    if row["catalog_id"] == "media.stash"
)


def configuration(config):
    doc = copy.deepcopy(config)
    doc.update(
        config_schema_version=3,
        fingerprint_version=3,
        catalogs=[CATALOG],
        definitions=build(),
        sources=[],
        bindings=[],
        contracts=[],
    )
    inputs = []
    for index in range(1, 5):
        for field in ("scene", "state"):
            source = f"source.synthetic.slot_{index}.{field}"
            doc["sources"].append(
                {
                    "source_id": source,
                    "source_type": "text",
                    "source_origin": "local",
                    "physical_source_key": source.replace("source.", "physical."),
                    "dependency_id": "dep.test",
                    "value_schema": {"type": "text", "required": True, "physical_state": False},
                    "freshness": {"mode": "event_stateful", "future_tolerance_s": 0},
                }
            )
            adapter = (
                {"kind": "ha_state", "ha_entity_id": f"media_player.synthetic_slot_{index}"}
                if field == "state"
                else {
                    "kind": "ha_attribute",
                    "ha_entity_id": f"media_player.synthetic_slot_{index}",
                    "ha_attribute": "scene_id",
                    "catalog_profile": "stash_slot",
                }
            )
            doc["bindings"].append(
                {
                    "binding_id": source.replace("source.", "binding."),
                    "source_id": source,
                    "adapter": adapter,
                    "transform": {"kind": "type_cast", "target": "text"},
                }
            )
        identifier = f"synthetic.slot_{index}"
        doc["contracts"].append(
            {
                "contract_id": identifier,
                "type_id": "media.stash_slot",
                "type_version": 1,
                "enabled": True,
                "parameters": {},
                "inputs": [
                    {"name": field, "ref": f"source.synthetic.slot_{index}.{field}"}
                    for field in ("scene", "state")
                ],
            }
        )
        inputs.extend(
            [
                {"name": f"slot_{index}_active", "ref": identifier + ".active"},
                {"name": f"slot_{index}_class", "ref": identifier + ".value"},
            ]
        )
    doc["contracts"].append(
        {
            "contract_id": "synthetic.stash",
            "type_id": "media.stash_class",
            "type_version": 3,
            "enabled": True,
            "parameters": {},
            "inputs": inputs,
        }
    )
    return upgrade_document(doc)


async def send(
    runtime,
    slot,
    scene="synthetic-scene",
    *,
    studio="Synthetic studio",
    state="playing",
    title="Synthetic title",
    artist="Synthetic person",
    kind="live_change",
    album=None,
):
    runtime.clock.advance(0.01)
    stamp = runtime.clock.now_utc().isoformat()
    adapter = BridgeAdapter(runtime, None, "ws://unused", "synthetic")
    adapter.sequence = runtime.state.generation + 1
    attributes = {"scene_id": scene, "title": title, "media_artist": artist}
    if studio is not None:
        attributes["studio"] = studio
    if album is not None:
        attributes["media_album_name"] = album
    value = {
        "state": state,
        "attributes": attributes,
        "last_changed": stamp,
        "last_updated": stamp,
        "last_reported": stamp,
    }
    for binding in runtime.config.bindings:
        if binding.source_id.startswith(f"source.synthetic.slot_{slot}."):
            await runtime.submit(
                "observation",
                adapter.observation(binding, value, kind, {"time_fired": stamp}, runtime.config),
            )


def scenes(runtime):
    return {
        row["key"]: row
        for row in runtime.state.tables["catalog_sighting"].values()
        if row["signal_type"] == "scene" and not row.get("hierarchy_master")
    }


def field(runtime, identifier, name="value"):
    return runtime.snapshot()["contracts"][identifier]["fields"][name]


async def care(runtime, identity, action="classify", classification="normal"):
    return await runtime.submit(
        "catalog_care_write",
        {
            "request": {
                "catalog_id": "media.stash",
                "definition_version": 2,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": head(runtime.state, runtime.config.catalogs[0])[
                    "revision"
                ],
                "request_id": str(uuid4()),
                "reason": "Synthetic care",
                "identities": [identity],
                "action": action,
                "classification": classification,
            }
        },
    )


async def test_four_simultaneous_slots_discover_one_studio_four_scenes_and_inherit(runtime, config):
    await activate(runtime, configuration(config))
    for index in range(1, 5):
        await send(runtime, index, f"synthetic-scene-{index}")
    assert scenes(runtime) == {}
    await advance(runtime, 2)
    assert len(scenes(runtime)) == 4
    assert sum(row["seen_count"] for row in scenes(runtime).values()) == 4
    catalog = runtime.config.catalogs[0]
    assert head(runtime.state, catalog)["revision"] == 0
    roots = listing(runtime.state, catalog, grouped=True)["items"]
    assert len(roots) == 1 and roots[0]["display_name"] == "Synthetic studio"
    assert roots[0]["seen_count"] == 0 and roots[0]["variant_count"] == 4
    children = detail(runtime.state, catalog, roots[0]["identity"])["variants"]
    assert {row["key"] for row in children} == set(scenes(runtime))
    assert all(
        row["classification"] is None and row["master_identity"] == roots[0]["identity"]
        for row in children
    )
    await care(runtime, roots[0]["identity"])
    assert all(
        field(runtime, f"synthetic.slot_{index}")["value"] == "normal" for index in range(1, 5)
    )
    assert sum(row["seen_count"] for row in scenes(runtime).values()) == 4
    # A new scene immediately uses the saved studio class before catalog flush.
    await send(runtime, 1, "synthetic-next")
    assert field(runtime, "synthetic.slot_1")["value"] == "normal"


async def test_same_scene_in_two_slots_counts_twice_pause_resume_and_display_never_recount(
    runtime, config
):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    await send(runtime, 2)
    await advance(runtime, 2)
    assert scenes(runtime)["synthetic-scene"]["seen_count"] == 2
    for state in ("paused", "playing"):
        await send(runtime, 1, state=state)
        await advance(runtime, 3)
    await send(runtime, 1, title="Synthetic corrected label", artist="Synthetic other person")
    await advance(runtime, 3)
    row = scenes(runtime)["synthetic-scene"]
    assert row["seen_count"] == 2
    assert row["display_metadata"] == {
        "title": "Synthetic corrected label",
        "artist": "Synthetic other person",
    }
    await send(runtime, 2, state="idle")
    await send(runtime, 2)
    await advance(runtime, 2)
    assert scenes(runtime)["synthetic-scene"]["seen_count"] == 3


async def test_album_fallback_and_scene_id_are_authoritative(runtime, config):
    await activate(runtime, configuration(config))
    await send(runtime, 1, 123, studio=None, album="Synthetic fallback studio")
    await advance(runtime, 2)
    assert set(scenes(runtime)) == {"123"}
    root = listing(runtime.state, runtime.config.catalogs[0], grouped=True)["items"][0]
    assert root["display_name"] == "Synthetic fallback studio"
    await send(runtime, 1, 124, studio=None, album="Synthetic fallback studio")
    await advance(runtime, 2)
    assert set(scenes(runtime)) == {"123", "124"}


@pytest.mark.parametrize("change", ["missing_studio", "missing_scene", "unavailable", "off"])
async def test_unfinished_or_incoherent_slot_never_flushes_stale_metadata(runtime, config, change):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    await advance(runtime, 0.5)
    if change == "missing_studio":
        await send(runtime, 1, studio=None)
    elif change == "missing_scene":
        await send(runtime, 1, None)
    else:
        await send(runtime, 1, state=change)
    await advance(runtime, 3)
    assert scenes(runtime) == {}


@pytest.mark.parametrize("operation", ["gap", "restore"])
async def test_slot_pending_is_baselined_across_gap_or_restore(runtime, config, operation):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    if operation == "gap":
        await runtime.submit("gap", "synthetic gap")
    else:
        await runtime._restore("synthetic restore")
    await advance(runtime, 3)
    await send(runtime, 1, kind="snapshot")
    await send(runtime, 1, kind="report")
    await advance(runtime, 3)
    assert scenes(runtime) == {}


async def test_saved_detachment_wins_and_tick_never_evaluates_contracts(
    runtime, config, monkeypatch
):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    states = copy.deepcopy(runtime.state.tables["contract_state_current"])

    def forbidden(*args, **kwargs):
        pytest.fail("catalog flush evaluated contracts")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_evaluate", forbidden)
        await advance(runtime, 2)
    assert runtime.state.tables["contract_state_current"] == states
    catalog = runtime.config.catalogs[0]
    rows = all_rows(runtime.state, catalog)
    scene = next(row for row in rows.values() if row["key"] == "synthetic-scene")
    await care(runtime, scene["identity"], "ungroup")
    await care(runtime, scene["master_identity"], classification="private")
    await send(runtime, 1)
    assert field(runtime, "synthetic.slot_1")["value"] == "normal"
    await send(runtime, 2, "synthetic-new")
    assert field(runtime, "synthetic.slot_2")["value"] == "private"


async def test_private_augmentation_keeps_catalog_data_and_replaces_aggregate_inputs(
    runtime, config
):
    doc = old_configuration(config, "media.stash_class")
    doc["dependencies"].append(
        {"dependency_id": "dep.synthetic_ha", "kind": "ha", "monitor": "internal"}
    )
    await activate(runtime, doc)
    package = upgrade_package(await export(runtime))
    before = copy.deepcopy(package)
    updated = augment(
        package, build(), [f"media_player.synthetic_slot_{index}" for index in range(1, 5)]
    )
    read_package(updated)
    checked = validate(updated["config"], runtime.types)
    assert package == before and checksum(updated["catalog_contents"]) == checksum(
        package["catalog_contents"]
    )
    slots = [row for row in checked.contracts if row.type_id == "media.stash_slot"]
    assert len(slots) == 4 and all(row.ha_projection for row in slots)
    combined = next(row for row in checked.contracts if row.type_id == "media.stash_class")
    assert combined.type_version == 3 and len(combined.inputs) == 8
    assert all(item.ref.startswith("media.stash.slot_") for item in combined.inputs)


async def test_reviewing_discovered_scene_saves_valid_unclassified_parent_atomically(
    runtime, config
):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    await advance(runtime, 2)
    catalog = runtime.config.catalogs[0]
    scene = next(
        row for row in all_rows(runtime.state, catalog).values() if row["key"] == "synthetic-scene"
    )
    await care(runtime, scene["identity"], "review", classification=None)
    current = head(runtime.state, catalog)
    assert current["revision"] == 1 and len(current["entries"]) == 2
    assert current["entries"][scene["identity"]]["master_identity"] == scene["master_identity"]
    assert current["entries"][scene["master_identity"]]["classification"] is None
    validate_heads(runtime.state, runtime.config)
    read_package(await export(runtime))


async def test_inactive_slots_are_not_applicable_and_unassigned_active_slot_is_private(
    runtime, config
):
    await activate(runtime, configuration(config))
    for index in range(1, 5):
        await send(runtime, index, state="off", scene=None, studio=None)
    assert field(runtime, "synthetic.stash")["status"] == "not_applicable"
    await send(runtime, 2)
    assert field(runtime, "synthetic.stash")["value"] == "private"
    assert not field(runtime, "synthetic.slot_2")["configuration"]["complete"]


async def test_single_entry_preview_and_failed_commit_keep_discovered_graph_atomic(runtime, config):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    await advance(runtime, 2)
    catalog = runtime.config.catalogs[0]
    scene = next(
        row for row in all_rows(runtime.state, catalog).values() if row["key"] == "synthetic-scene"
    )
    request = {
        "catalog_id": catalog.catalog_id,
        "definition_version": catalog.version,
        "expected_active_revision": runtime.state.active_revision,
        "expected_content_revision": 0,
        "request_id": str(uuid4()),
        "reason": "Synthetic scene editor",
        "changes": [
            {
                **{
                    key: scene[key]
                    for key in ("scope", "media_type", "signal_type", "key", "master_key")
                },
                "classification": None,
                "note": "Synthetic note",
            }
        ],
    }
    before = runtime.state.model_dump()
    persisted = runtime.store.state.model_dump()
    await runtime.submit("catalog_preview", {"request": request})
    assert runtime.state.model_dump() == before

    def fail():
        raise RuntimeError("synthetic commit failure")

    runtime.store.before_commit = fail
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await runtime.submit("catalog_write", {"request": request})
    runtime.store.before_commit = None
    assert runtime.state.model_dump() == before
    assert runtime.store.state.model_dump() == persisted
    assert not runtime.persistence
    await runtime.submit("recover")
    assert runtime.persistence
    await runtime.submit("catalog_write", {"request": request})
    validate_heads(runtime.state, runtime.config)
    assert len(head(runtime.state, catalog)["entries"]) == 2


async def test_discovered_children_enforce_two_levels_before_any_saved_class(runtime, config):
    await activate(runtime, configuration(config))
    await send(runtime, 1)
    await advance(runtime, 2)
    catalog = runtime.config.catalogs[0]
    studio = listing(runtime.state, catalog, grouped=True)["items"][0]
    shared = {key: studio[key] for key in ("scope", "media_type", "signal_type")}
    before = copy.deepcopy(head(runtime.state, catalog))
    with pytest.raises(ValueError, match="catalog_master_invalid"):
        await runtime.submit(
            "catalog_write",
            {
                "request": {
                    "catalog_id": catalog.catalog_id,
                    "definition_version": catalog.version,
                    "expected_active_revision": runtime.state.active_revision,
                    "expected_content_revision": 0,
                    "request_id": str(uuid4()),
                    "reason": "Synthetic third level",
                    "changes": [
                        {**shared, "key": "synthetic-upper", "classification": "normal"},
                        {**shared, "key": studio["key"], "master_key": "synthetic-upper"},
                    ],
                }
            },
        )
    assert head(runtime.state, catalog) == before


def test_slot_profile_is_explicit_versioned_and_metadata_bounded():
    for row in build():
        check_definition(DeclarativeDefinition.model_validate(row))
    old = build()[0]
    old["language_version"] = 6
    with pytest.raises(ValueError, match="language_version_7_required"):
        check_definition(DeclarativeDefinition.model_validate(old))
    with pytest.raises(ValueError, match="catalog_profile_requires_scene_binding"):
        AdapterConfig(
            kind="ha_state", ha_entity_id="media_player.synthetic", catalog_profile="stash_slot"
        )
