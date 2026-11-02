"""Synthetic P7 probes; execution deferred until the final b22 package."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.adapters import AdapterConfig
from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.apple_tv_catalog import AppleTitleConfig, AppTitleRule, parse
from core_contracts.apple_tv_package import augment
from core_contracts.catalog_care import all_rows, detail, listing
from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.freshness_templates import upgrade_document
from core_contracts.interface_upgrade import upgrade_package
from core_contracts.managed_catalog import head, validate_heads
from core_contracts.model import checksum
from core_contracts.portable import read_package
from core_contracts.registry import validate
from dev.build_apple_tv_templates import build, catalog
from dev.build_apple_tv_templates import settings as defaults

from .conftest import activate
from .test_beta_media import configuration as old_configuration
from .test_catalog_playback import advance
from .test_portable import export


def settings(mode="standalone", **kwargs):
    return AppleTitleConfig(apps=(AppTitleRule(app_id="synthetic.app", mode=mode),), **kwargs)


def configuration(config, options=None, extra_app_binding=False):
    options = options or settings()
    doc = copy.deepcopy(config)
    doc.update(
        config_schema_version=3,
        fingerprint_version=3,
        catalogs=[catalog()],
        definitions=build(),
        sources=[],
        bindings=[],
        contracts=[],
    )
    for field in ("title", "state", *(("app",) if extra_app_binding else ())):
        identifier = "source.synthetic.apple_" + field
        doc["sources"].append(
            {
                "source_id": identifier,
                "source_type": "text",
                "source_origin": "local",
                "physical_source_key": identifier.replace("source.", "physical."),
                "dependency_id": "dep.test",
                "value_schema": {"type": "text", "required": True, "physical_state": False},
                "freshness": {"mode": "event_stateful", "future_tolerance_s": 0},
            }
        )
        adapter = (
            {"kind": "ha_state", "ha_entity_id": "media_player.synthetic_apple"}
            if field == "state"
            else {
                "kind": "ha_attribute",
                "ha_entity_id": "media_player.synthetic_apple",
                "ha_attribute": "media_title" if field == "title" else "app_id",
                **(
                    {
                        "catalog_profile": "apple_tv_title",
                        "title_catalog": options.model_dump(mode="json"),
                    }
                    if field == "title"
                    else {}
                ),
            }
        )
        doc["bindings"].append(
            {
                "binding_id": identifier.replace("source.", "binding."),
                "source_id": identifier,
                "adapter": adapter,
                "transform": {"kind": "type_cast", "target": "text"},
            }
        )
    doc["contracts"] = [
        {
            "contract_id": "synthetic.apple",
            "type_id": "media.appletv_title_class",
            "type_version": 1,
            "enabled": True,
            "parameters": {},
            "inputs": [
                {"name": name, "ref": "source.synthetic.apple_" + name}
                for name in ("title", "state")
            ],
        }
    ]
    return upgrade_document(doc)


async def send(
    runtime,
    title="Synthetic programme",
    second="",
    *,
    app_id="synthetic.app",
    app_name="Synthetic app",
    content_id="",
    state="playing",
    kind="live_change",
):
    runtime.clock.advance(0.01)
    stamp = runtime.clock.now_utc().isoformat()
    adapter = BridgeAdapter(runtime, None, "ws://unused", "synthetic")
    adapter.sequence = runtime.state.generation + 1
    value = {
        "state": state,
        "attributes": {
            "media_title": title,
            "media_artist": second,
            "media_content_id": content_id,
            "app_id": app_id,
            "app_name": app_name,
        },
        "last_changed": stamp,
        "last_updated": stamp,
        "last_reported": stamp,
    }
    for binding in runtime.config.bindings:
        await runtime.submit(
            "observation",
            adapter.observation(binding, value, kind, {"time_fired": stamp}, runtime.config),
        )


def items(runtime):
    return [
        row
        for row in runtime.state.tables["catalog_sighting"].values()
        if not row.get("hierarchy_master")
    ]


def value(runtime):
    return runtime.snapshot()["contracts"]["synthetic.apple"]["fields"]["value"]


async def classify(runtime, identity, classification="private", action="classify"):
    cat = runtime.config.catalogs[0]
    await runtime.submit(
        "catalog_care_write",
        {
            "request": {
                "catalog_id": cat.catalog_id,
                "definition_version": cat.version,
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": head(runtime.state, cat)["revision"],
                "request_id": str(uuid4()),
                "reason": "Synthetic title care",
                "identities": [identity],
                "action": action,
                "classification": classification,
            }
        },
    )


@pytest.mark.parametrize(
    ("mode", "title", "second", "content_id", "parent", "child"),
    [
        (
            "episode_in_second",
            "Synthetic series - part",
            "Synthetic episode",
            "",
            "Synthetic series - part",
            "Synthetic episode",
        ),
        (
            "parent_in_second",
            "Synthetic video - part",
            "Synthetic channel",
            "",
            "Synthetic channel",
            "Synthetic video - part",
        ),
        (
            "content_prefix",
            "Synthetic series - part - Synthetic episode",
            "",
            "GN_EP_SYNTHETIC",
            "Synthetic series - part",
            "Synthetic episode",
        ),
        (
            "content_prefix",
            "Synthetic film - part",
            "Synthetic extra",
            "GN_MV_SYNTHETIC",
            None,
            "Synthetic film - part",
        ),
        (
            "standalone",
            "Synthetic complete - label",
            "Synthetic extra",
            "",
            None,
            "Synthetic complete - label",
        ),
    ],
)
async def test_each_configured_pattern_uses_exactly_two_levels_or_one(
    runtime, config, mode, title, second, content_id, parent, child
):
    await activate(runtime, configuration(config, settings(mode)))
    await send(runtime, title, second, content_id=content_id)
    await advance(runtime, 2)
    assert len(items(runtime)) == 1 and items(runtime)[0]["seen_count"] == 1
    cat = runtime.config.catalogs[0]
    roots = listing(runtime.state, cat, grouped=True)["items"]
    assert len(roots) == 1 and roots[0]["display_name"] == (parent or child)
    if parent:
        variants = detail(runtime.state, cat, roots[0]["identity"])["variants"]
        assert len(variants) == 1 and variants[0]["display_metadata"]["title"] == child
        assert variants[0]["master_identity"] == roots[0]["identity"]
    else:
        assert not roots[0].get("master_identity") and roots[0]["variant_count"] == 0
    assert head(runtime.state, cat)["revision"] == 0


async def test_live_tv_waits_sixty_seconds_without_splitting_and_sender_is_only_metadata(
    runtime, config
):
    await activate(runtime, configuration(config, settings("content_prefix")))
    await send(runtime, "Synthetic live - programme", "Synthetic sender")
    await advance(runtime, 30)
    await send(
        runtime,
        "Synthetic live - programme",
        "Synthetic sender",
        app_name="Synthetic renamed app",
        kind="report",
    )
    await advance(runtime, 29.9)
    assert items(runtime) == []
    await advance(runtime, 0.1)
    assert len(items(runtime)) == 1
    row = items(runtime)[0]
    assert row["display_name"] == "Synthetic live - programme" and not row.get("master_key")
    assert (
        row["catalog_metadata"]["live_tv"]
        and row["catalog_metadata"]["sender"] == "Synthetic sender"
    )
    await send(runtime, "Synthetic live - programme", "Synthetic sender", kind="report")
    await advance(runtime, 65)
    assert items(runtime)[0]["seen_count"] == 1
    cat = runtime.config.catalogs[0]
    assert (
        listing(runtime.state, cat, filters={"live_tv": "true", "sender": "Synthetic sender"})[
            "total"
        ]
        == 1
    )
    assert listing(runtime.state, cat, filters={"live_tv": "false"})["total"] == 0


async def test_new_episode_inherits_known_master_immediately_without_extra_revision(
    runtime, config
):
    await activate(runtime, configuration(config, settings("episode_in_second")))
    await send(runtime, "Synthetic series", "Synthetic episode one")
    await advance(runtime, 2)
    cat = runtime.config.catalogs[0]
    parent = listing(runtime.state, cat, grouped=True)["items"][0]
    await classify(runtime, parent["identity"])
    before = copy.deepcopy(head(runtime.state, cat))
    await send(runtime, "Synthetic series", "Synthetic episode two")
    assert value(runtime)["value"] == "private" and value(runtime)["configuration"]["complete"]
    await advance(runtime, 10)
    variants = detail(runtime.state, cat, parent["identity"])["variants"]
    assert len(variants) == 2 and all(
        row["effective_classification"] == "private" for row in variants
    )
    assert head(runtime.state, cat) == before
    validate_heads(runtime.state, runtime.config)


async def test_split_transition_and_app_switch_never_capture_a_mixed_pair(runtime, config):
    await activate(runtime, configuration(config, settings("episode_in_second")))
    await send(runtime, "Synthetic series one", "Synthetic episode one")
    await advance(runtime, 2)
    await send(runtime, "Synthetic series two", "Synthetic episode one")
    await advance(runtime, 8)
    assert len(items(runtime)) == 1
    await send(runtime, "Synthetic series two", "Synthetic episode two")
    await advance(runtime, 2)
    assert len(items(runtime)) == 2
    await send(runtime, "Synthetic leftover", state="paused", app_id="synthetic.other")
    await advance(runtime, 20)
    assert len(items(runtime)) == 2


async def test_pause_resume_same_report_and_display_name_do_not_recount(runtime, config):
    await activate(runtime, configuration(config))
    await send(runtime)
    await advance(runtime, 2)
    for state in ("paused", "playing", "playing"):
        await send(runtime, state=state, app_name="Synthetic renamed app", kind="report")
        await advance(runtime, 3)
    assert items(runtime)[0]["seen_count"] == 1
    assert items(runtime)[0]["catalog_metadata"]["app_name"] == "Synthetic renamed app"
    await send(runtime, state="idle")
    await send(runtime)
    await advance(runtime, 2)
    assert items(runtime)[0]["seen_count"] == 2


async def test_source_and_app_stability_are_independent_and_app_ids_namespace_titles(
    runtime, config
):
    options = AppleTitleConfig(
        stability_s=3,
        apps=(AppTitleRule(app_id="synthetic.app", mode="standalone", stability_s=7),),
    )
    await activate(runtime, configuration(config, options))
    await send(runtime)
    await advance(runtime, 6)
    assert items(runtime) == []
    await advance(runtime, 1)
    assert len(items(runtime)) == 1
    await send(runtime, app_id="synthetic.other")
    await advance(runtime, 2)
    assert len(items(runtime)) == 1
    await advance(runtime, 1)
    assert len(items(runtime)) == 2
    assert len({row["key"] for row in items(runtime)}) == 2


@pytest.mark.parametrize(
    ("mode", "title", "second", "content_id"),
    [
        ("ignore", None, "", ""),
        ("content_prefix", "Synthetic season", "", "GN_SEASON_SYNTHETIC"),
        ("parent_in_second", "", "Synthetic channel", ""),
        ("episode_in_second", "Synthetic series", "", ""),
    ],
)
async def test_ignored_or_incomplete_titles_are_never_discovered(
    runtime, config, mode, title, second, content_id
):
    await activate(runtime, configuration(config, settings(mode)))
    await send(runtime, title, second, content_id=content_id)
    await advance(runtime, 70)
    assert items(runtime) == []
    if mode == "ignore" or content_id.startswith("GN_SEASON"):
        assert value(runtime)["status"] == "not_applicable"


async def test_invalid_next_frame_cancels_pending_and_generic_app_frames_do_not_overwrite_it(
    runtime, config
):
    await activate(runtime, configuration(config, extra_app_binding=True))
    await send(runtime)
    await advance(runtime, 2)
    assert len(items(runtime)) == 1
    await send(runtime, "Synthetic unfinished")
    await advance(runtime, 0.5)
    await send(runtime, "")
    await advance(runtime, 3)
    assert len(items(runtime)) == 1


@pytest.mark.parametrize("operation", ["gap", "restore", "snapshot"])
async def test_baseline_does_not_count_pending_titles(runtime, config, operation):
    await activate(runtime, configuration(config))
    await send(runtime)
    if operation == "gap":
        await runtime.submit("gap", "synthetic gap")
    elif operation == "restore":
        await runtime._restore("synthetic restore")
    else:
        await send(runtime, kind="snapshot")
    await advance(runtime, 3)
    await send(runtime, kind="snapshot")
    await send(runtime, kind="report")
    await advance(runtime, 3)
    assert items(runtime) == []


async def test_saved_detachment_wins_and_explicit_scene_care_exports_complete_graph(
    runtime, config
):
    await activate(runtime, configuration(config, settings("parent_in_second")))
    await send(runtime, "Synthetic video", "Synthetic channel")
    await advance(runtime, 2)
    cat = runtime.config.catalogs[0]
    scene = next(row for row in all_rows(runtime.state, cat).values() if row.get("master_identity"))
    await classify(runtime, scene["identity"], classification=None, action="review")
    validate_heads(runtime.state, runtime.config)
    read_package(await export(runtime))
    await classify(runtime, scene["identity"], classification="normal", action="ungroup")
    await classify(runtime, scene["master_identity"])
    await send(runtime, "Synthetic video", "Synthetic channel")
    assert value(runtime)["value"] == "normal"


async def test_title_flush_never_evaluates_contracts(runtime, config, monkeypatch):
    await activate(runtime, configuration(config))
    await send(runtime)
    before = copy.deepcopy(runtime.state.tables["contract_state_current"])

    def forbidden(*args, **kwargs):
        pytest.fail("title flush evaluated contracts")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_evaluate", forbidden)
        await advance(runtime, 2)
    assert runtime.state.tables["contract_state_current"] == before


async def test_reviewing_inherited_episode_does_not_change_its_semantic_token(runtime, config):
    await activate(runtime, configuration(config, settings("episode_in_second")))
    await send(runtime, "Synthetic series", "Synthetic episode")
    await advance(runtime, 2)
    cat = runtime.config.catalogs[0]
    parent = listing(runtime.state, cat, grouped=True)["items"][0]
    await classify(runtime, parent["identity"])
    child = detail(runtime.state, cat, parent["identity"])["variants"][0]
    before = [
        token for token in value(runtime)["evidence"] if ":" + child["identity"] + ":" in token
    ]
    await classify(runtime, child["identity"], classification=None, action="review")
    await send(runtime, "Synthetic series", "Synthetic episode")
    after = [
        token for token in value(runtime)["evidence"] if ":" + child["identity"] + ":" in token
    ]
    assert before and after == before


async def test_import_adds_only_title_configuration_and_preserves_catalog_contents(runtime, config):
    doc = old_configuration(config, "media.appletv_class")
    doc["dependencies"].append(
        {"dependency_id": "dep.synthetic_ha", "kind": "ha", "monitor": "internal"}
    )
    await activate(runtime, doc)
    package = upgrade_package(await export(runtime))
    before = copy.deepcopy(package)
    updated = augment(package, build(), catalog(), "media_player.synthetic_apple", defaults())
    read_package(updated)
    checked = validate(updated["config"], runtime.types)
    assert package == before and checksum(updated["catalog_contents"]) == checksum(
        package["catalog_contents"]
    )
    title = next(row for row in checked.contracts if row.type_id == "media.appletv_title_class")
    assert title.ha_projection and title.contract_id == "media.appletv.title_class"
    previous = next(row for row in checked.contracts if row.type_id == "media.appletv_class")
    assert previous.model_dump(mode="json") == next(
        row
        for row in validate(before["config"], runtime.types).contracts
        if row.type_id == "media.appletv_class"
    ).model_dump(mode="json")


def test_modes_versions_bounds_and_schema_are_closed_and_compatible():
    check_definition(DeclarativeDefinition.model_validate(build()[0]))
    old = build()[0]
    old["language_version"] = 7
    with pytest.raises(ValueError, match="language_version_8_required"):
        check_definition(DeclarativeDefinition.model_validate(old))
    for bad in (0, -1, 3601, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            AppleTitleConfig(stability_s=bad)
    with pytest.raises(ValueError, match="apple_tv_app_duplicate"):
        AppleTitleConfig(
            apps=(
                AppTitleRule(app_name="Synthetic app", mode="ignore"),
                AppTitleRule(app_name="synthetic APP", mode="standalone"),
            )
        )
    with pytest.raises(ValueError):
        AppTitleRule(app_id="synthetic.app", mode="unknown")
    with pytest.raises(ValueError, match="apple_tv_title_binding_required"):
        AdapterConfig(
            kind="ha_state",
            ha_entity_id="media_player.synthetic",
            catalog_profile="apple_tv_title",
            title_catalog=settings(),
        )
    old_adapter = AdapterConfig(kind="ha_state", ha_entity_id="media_player.synthetic")
    assert "title_catalog" not in old_adapter.model_dump(mode="json")
    assert "catalog_profile" not in old_adapter.model_dump(mode="json")
    generated = json.loads(
        (Path(__file__).parents[1] / "examples/apple-tv-templates-b22.json").read_text(
            encoding="utf-8"
        )
    )
    assert generated["settings_schema"] == AppleTitleConfig.model_json_schema()
    assert generated["definitions"] == build() and generated["catalog"] == catalog()


async def test_import_reuses_existing_physical_sources_and_preserves_their_ids(runtime, config):
    doc = old_configuration(config, "media.appletv_class")
    doc["dependencies"].append(
        {"dependency_id": "dep.synthetic_ha", "kind": "ha", "monitor": "internal"}
    )
    for name in ("title", "state"):
        source_id = "source.synthetic.existing_" + name
        rule = {"mode": "event_stateful", "future_tolerance_s": 0}
        doc["sources"].append(
            {
                "source_id": source_id,
                "source_type": "text",
                "source_origin": "local",
                "physical_source_key": "physical.synthetic.existing_" + name,
                "dependency_id": "dep.synthetic_ha",
                "value_schema": {"type": "text", "required": True, "physical_state": False},
                **(
                    {"freshness_selection": {"kind": "own", "rule": rule}}
                    if doc["config_schema_version"] == 4
                    else {"freshness": rule}
                ),
            }
        )
        adapter = (
            {"kind": "ha_state", "ha_entity_id": "media_player.synthetic_apple"}
            if name == "state"
            else {
                "kind": "ha_attribute",
                "ha_entity_id": "media_player.synthetic_apple",
                "ha_attribute": "media_title",
            }
        )
        doc["bindings"].append(
            {
                "binding_id": "binding.synthetic.existing_" + name,
                "source_id": source_id,
                "adapter": adapter,
                "transform": {"kind": "type_cast", "target": "text"},
            }
        )
    await activate(runtime, doc)
    package = upgrade_package(await export(runtime))
    updated = augment(package, build(), catalog(), "media_player.synthetic_apple", defaults())
    validate(updated["config"], runtime.types)
    assert len(updated["config"]["sources"]) == len(package["config"]["sources"])
    assert len(updated["config"]["bindings"]) == len(package["config"]["bindings"])
    contract = next(
        row
        for row in updated["config"]["contracts"]
        if row["contract_id"] == "media.appletv.title_class"
    )
    assert {row["ref"] for row in contract["inputs"]} == {
        "source.synthetic.existing_title",
        "source.synthetic.existing_state",
    }
    original = next(
        row
        for row in package["config"]["bindings"]
        if row["binding_id"] == "binding.synthetic.existing_title"
    )
    updated_binding = next(
        row for row in updated["config"]["bindings"] if row["binding_id"] == original["binding_id"]
    )
    assert original["adapter"].get("catalog_profile") is None
    assert updated_binding["adapter"]["catalog_profile"] == "apple_tv_title"
    assert updated_binding["source_id"] == original["source_id"]


def test_configured_app_id_precedes_name_and_known_no_title_apps_are_ignored():
    options = AppleTitleConfig(
        apps=(
            AppTitleRule(app_id="synthetic.app", mode="ignore"),
            AppTitleRule(app_name="Synthetic app", mode="standalone"),
        )
    )
    assert parse(
        {"media_title": "Synthetic item", "app_id": "synthetic.app", "app_name": "Synthetic app"},
        options,
    )["title_ignored"]
    for name in ("Netflix", "Fitness", "stashyTV"):
        assert parse(
            {"media_title": "Synthetic item", "app_id": "synthetic.app", "app_name": name},
            defaults(),
        )["title_ignored"]
