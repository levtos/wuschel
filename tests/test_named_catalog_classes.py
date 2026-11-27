"""Synthetic evidence for the #169 migration and freshness compatibility."""

from copy import deepcopy
from uuid import uuid4

import pytest

from core_contracts.extensions.catalog.catalog_class_rollback import (
    PreparedClassRollback,
    prepare_work_rollback,
)
from core_contracts.extensions.catalog.catalog_class_rollback import preview as rollback_preview
from core_contracts.extensions.catalog.managed_catalog import effective, head, identity
from core_contracts.extensions.catalog.named_class_migration import build_package
from core_contracts.freshness_templates import propose_sensor_kinds, upgrade_document
from core_contracts.portable import objects, read_package
from core_contracts.registry import (
    assessment_fingerprints,
    observation_fingerprints,
    storage_document,
)
from core_contracts.validation import validate_document
from tests.conftest import activate, activate_draft
from tests.test_beta4_freshness import legacy, running
from tests.test_managed_catalog import change, configured, request
from tests.test_portable import export, plan, stage
from tests.test_postgres import database as database


@pytest.mark.parametrize("prepared", [False, True])
async def test_typed_migration_commits_one_content_revision(runtime, config, prepared):
    document = configured(config)
    document["catalogs"][0].update(catalog_id="media.homepods", unassigned_value="normal")
    document["catalogs"][0]["classification_schema"]["values"] = [
        "normal",
        "boost",
        "stumm",
        "leiser",
    ]
    await activate(runtime, document)
    old = runtime.config.catalogs[0]
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                **request(
                    runtime,
                    [
                        change("Synthetic A", classification="stumm"),
                        change("Synthetic B", classification=None, master_key="Synthetic A"),
                        change("Synthetic C", classification="leiser"),
                        change("Synthetic D", classification=None),
                        change("Synthetic E", classification="normal"),
                        change("Synthetic F", classification="boost"),
                    ],
                ),
                "catalog_id": old.catalog_id,
            }
        },
    )
    package = build_package(await export(runtime))
    assert package["format_version"] == 3
    assert package["config"]["config_schema_version"] == 5
    assert package["config"]["fingerprint_version"] == 4
    assert read_package(package) == package
    keep = {key: "file" for key in objects(package["config"])}
    preview = await plan(runtime, package, resolutions=keep)
    assert preview["unresolved"] == []
    assert preview["catalog_migrations"][0]["class_values"] == {
        "stumm": {"damped": True},
        "leiser": {"volume_offset": -10},
        "normal": {},
        "boost": {"volume_offset": 15},
    }
    draft = await stage(runtime, package, preview, resolutions=keep)
    assert runtime.config.catalogs[0].version == 1
    await activate_draft(runtime, draft)
    new = runtime.config.catalogs[0]
    current = head(runtime.state, new)
    assert current["revision"] == 1
    entries = current["entries"]
    keys = [identity("example", "music", "title", f"Synthetic {label}") for label in "ABCDEF"]
    assert [entries[key].get("class_values") for key in keys] == [
        {"damped": True},
        None,
        {"volume_offset": -10},
        None,
        None,
        {"volume_offset": 15},
    ]
    assert effective(entries, keys[0], new, "volume_offset")[:2] == (0, False)
    assert effective(entries, keys[1], new, "damped") == (True, True, keys[0])
    assert effective(entries, keys[2], new, "volume_offset")[:2] == (-10, True)
    assert effective(entries, keys[3], new, "damped")[:2] == (False, False)
    assert effective(entries, keys[4], new, "volume_offset")[:2] == (0, False)
    assert effective(entries, keys[5], new, "damped")[:2] == (False, False)
    assert head(runtime.state, old)["entries"][keys[0]]["classification"] == "stumm"
    assert read_package(await export(runtime))["format_version"] == 3
    with pytest.raises(ValueError, match="catalog_class_value_invalid"):
        await runtime.submit(
            "catalog_write",
            {
                "request": {
                    **request(
                        runtime,
                        [
                            change(
                                "Synthetic C",
                                classification=None,
                                class_values={"volume_offset": True},
                            )
                        ],
                        revision=1,
                    ),
                    "catalog_id": new.catalog_id,
                    "definition_version": new.version,
                    "request_id": str(uuid4()),
                }
            },
        )
    rollback = rollback_preview(runtime.state, runtime.config)
    assert rollback["catalogs"][0]["care_revisions"] == 0
    body = {
        "expected_active_revision": runtime.state.active_revision,
        "revision": rollback["target_revision"],
        "catalog_migration_revision": rollback["migration_revision"],
        "plan_digest": rollback["plan_digest"],
        "request_id": str(uuid4()),
    }
    with pytest.raises(ValueError, match="catalog_migration_rollback_required"):
        await runtime.submit("rollback", {"expected_active_revision": 2, "revision": 1})
    if prepared:
        prepared_plan = await prepare_work_rollback(runtime, body)
        assert isinstance(prepared_plan, PreparedClassRollback)
        assert runtime.state.active_revision == 2
        sealed = PreparedClassRollback.model_validate_json(prepared_plan.model_dump_json())
        result = await runtime.submit("catalog_class_rollback_prepared", sealed)
        assert await runtime.submit("catalog_class_rollback_prepared", sealed) == result
    else:
        result = await runtime.submit("rollback", body)
    assert result["revision"] == 3
    assert await runtime.submit("rollback", body) == result
    restored = runtime.config.catalogs[0]
    assert head(runtime.state, restored)["revision"] == 2
    assert head(runtime.state, restored)["entries"][keys[0]]["classification"] == "stumm"


async def test_bulk_changes_one_dimension_and_reconfiguration_keeps_old_values(runtime, config):
    from core_contracts.extensions.catalog.named_class_migration import configure_package

    document = configured(config)
    document["catalogs"][0].update(catalog_id="media.homepods", unassigned_value="normal")
    document["catalogs"][0]["classification_schema"]["values"] = [
        "normal",
        "boost",
        "mute",
        "quieter",
    ]
    await activate(runtime, document)
    package = build_package(await export(runtime))
    choices = {key: "file" for key in objects(package["config"])}
    migration = await plan(runtime, package, resolutions=choices)
    await activate_draft(runtime, await stage(runtime, package, migration, resolutions=choices))
    catalog = runtime.config.catalogs[0]
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                **request(
                    runtime,
                    [
                        change(
                            "Synthetic",
                            classification=None,
                            class_values={"volume_offset": 15, "damped": False},
                        )
                    ],
                    revision=1,
                ),
                "catalog_id": catalog.catalog_id,
                "definition_version": catalog.version,
            }
        },
    )
    key = identity("example", "music", "title", "Synthetic")
    care = {
        "catalog_id": catalog.catalog_id,
        "definition_version": catalog.version,
        "expected_active_revision": 2,
        "expected_content_revision": 2,
        "request_id": str(uuid4()),
        "reason": "Synthetic dimension",
        "action": "classify",
        "identities": [key],
        "class_key": "damped",
        "class_value": True,
    }
    impact = await runtime.submit("catalog_care_preview", {"request": care})
    assert impact["affected_keys"] == 1
    await runtime.submit("catalog_care_write", {"request": care})
    before = head(runtime.state, catalog)
    assert before["entries"][key]["class_values"] == {"damped": True, "volume_offset": 15}
    baseline = await export(runtime)
    edited = configure_package(
        baseline,
        baseline["config"]["catalog_classes"],
        {"media.homepods": [{"class_key": "volume_offset"}]},
    )
    choices = {key: "file" for key in objects(edited["config"])}
    preview = await plan(runtime, edited, resolutions=choices)
    assert preview["catalog_migrations"][0]["removed_values"] == {"damped": 1}
    await activate_draft(runtime, await stage(runtime, edited, preview, resolutions=choices))
    assert head(runtime.state, runtime.config.catalogs[0])["entries"][key]["class_values"] == {
        "volume_offset": 15
    }
    assert head(runtime.state, catalog) == before
    assert rollback_preview(runtime.state, runtime.config)["catalogs"][0]["care_revisions"] == 3


@pytest.mark.parametrize(
    ("catalog_id", "legacy_value", "class_key", "class_value"),
    [
        ("media.ps5", "heimkino", "sound_mode", "home_cinema"),
        ("media.pc", "headset", "sound_mode", "headset"),
        ("media.stash", "privat", "content", "private"),
        ("media.appletv", "normal", "content", "normal"),
        ("media.appletv_titles", "private", "content", "private"),
        ("media.tv_device", "ps5", "tv_device", "ps5"),
        ("media.tv_activity", "gaming", "tv_activity", "gaming"),
    ],
)
def test_migration_only_persists_the_legacy_dimension(
    config, catalog_id, legacy_value, class_key, class_value
):
    from core_contracts.extensions.catalog.named_class_migration import (
        CLASSES,
        convert_entry,
        upgrade_catalog,
    )
    from core_contracts.registry import RegistryConfig
    from core_contracts.rules.catalog_models import ManagedCatalog

    document = upgrade_document(configured(config), version=5)
    catalog = ManagedCatalog.model_validate({**document["catalogs"][0], "catalog_id": catalog_id})
    document["catalogs"] = [upgrade_catalog(catalog)]
    document["catalog_classes"] = deepcopy(list(CLASSES))
    target = RegistryConfig.model_validate(document).catalogs[0]
    migrated = convert_entry({"classification": legacy_value}, target)
    assert migrated == {"classification": None, "class_values": {class_key: class_value}}


def test_presence_device_classes_propose_motion(config):
    from core_contracts.contract_types import type_registry

    registry = validate_document(
        upgrade_document(config, version=5), type_registry(include_test_types=True)
    )
    for device_class in ("occupancy", "presence"):
        proposals = propose_sensor_kinds(
            registry, [{"entity_id": "sensor.example_1", "device_class": device_class, "unit": "W"}]
        )
        assert proposals["source.a"] == {"sensor_kind": "motion", "basis": "device_class"}


async def test_publish_new_class_in_selected_contract_and_ha_projection(
    runtime, config, aiohttp_client, tmp_path
):
    from core_contracts.ha_projection import snapshot

    from .test_declarative import definition, registry, schema
    from .test_managed_catalog import lookup_rule
    from .test_workbench_api import api_client

    lookup = {**lookup_rule("Synthetic First"), "class_key": "damped", "schema": schema()}
    template = {**definition(lookup), "language_version": 4}
    document = upgrade_document(configured(registry(config, template)), version=5)
    document["catalogs"][0]["class_assignments"] = [{"class_key": "damped"}]
    document["catalog_classes"] = [
        {"class_key": "damped", "display_name": "Dämpfen", "kind": "boolean", "default": False}
    ]
    document["contracts"][0]["ha_projection"] = True
    document["contracts"].append(
        {**deepcopy(document["contracts"][0]), "contract_id": "fixture.other"}
    )
    await activate(runtime, document)
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get("/api/v1/catalogs/classes/outputs", headers=headers)
    assert response.status == 200
    assert {(row["contract_id"], row["from_field"]) for row in await response.json()} == {
        ("fixture.echo", "value"),
        ("fixture.other", "value"),
    }

    response = await client.post(
        "/api/v1/catalogs/classes/migration",
        headers=headers,
        json={
            "expected_active_revision": runtime.state.active_revision,
            "catalog_classes": [
                *document["catalog_classes"],
                {
                    "class_key": "offset",
                    "display_name": "Korrektur",
                    "kind": "integer",
                    "default": 0,
                    "minimum": -30,
                    "maximum": 30,
                    "unit": "Punkte",
                },
            ],
            "assignments": {"example.titles": [{"class_key": "damped"}, {"class_key": "offset"}]},
            "outputs": [
                {
                    "catalog_id": "example.titles",
                    "class_key": "offset",
                    "contract_id": "fixture.echo",
                    "from_field": "value",
                }
            ],
        },
    )
    result = await response.json()
    assert response.status == 200, result
    assert result["preview"].get("can_stage") is not False
    assert result["preview"]["unresolved"] == []
    (projection,) = result["projections"]
    assert projection["contract_id"] == "fixture.echo"
    assert projection["primary_field"] == "value"
    assert projection["old_fields"] == ["value"]
    assert set(projection["fields"]) == {"value", "offset"}
    draft = await stage(
        runtime, result["package"], result["preview"], resolutions=result["resolutions"]
    )
    assert runtime.config.contracts[0].type_version == 1
    await activate_draft(runtime, draft)
    selected, sibling = runtime.config.contracts
    assert selected.type_version > sibling.type_version
    catalog = runtime.config.catalogs[0]
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                **request(
                    runtime,
                    [change(classification=None, class_values={"offset": 12})],
                    revision=head(runtime.state, catalog)["revision"],
                ),
                "definition_version": catalog.version,
            }
        },
    )
    projected = {row["contract_id"]: row for row in snapshot(runtime)["contracts"]}
    offset = projected["fixture.echo"]["fields"]["offset"]
    assert (offset["value"], offset["status"], offset["unit"]) == (12, "valid", "Punkte")
    assert set(projected["fixture.other"]["fields"]) == {"value"}


def test_title_classifier_keeps_untouched_dimensions_for_unknown_numbers(config):
    from core_contracts.extensions.catalog.named_class_migration import CLASSES, upgrade_catalog
    from core_contracts.extensions.tc_migration.title_transfer import mapped_classes, write_classes
    from core_contracts.registry import RegistryConfig
    from core_contracts.rules.catalog_models import ManagedCatalog

    document = upgrade_document(configured(config), version=5)
    document["catalog_classes"] = deepcopy(list(CLASSES))
    look = next(row for row in document["catalog_classes"] if row["class_key"] == "light_look")
    look["values"]["soft"] = {"display_name": "Sanft", "description": "Das Licht ist gedämpft."}
    shape = document["catalogs"][0]
    updated = []
    for catalog_id, mapping_value in [("media.ps5", "soft"), ("media.pc", "standard")]:
        row = upgrade_catalog(ManagedCatalog.model_validate({**shape, "catalog_id": catalog_id}))
        next(item for item in row["class_assignments"] if item["class_key"] == "light_look")[
            "title_catalog_mapping"
        ] = {"2": mapping_value}
        updated.append(row)
    document["catalogs"] = updated
    ps5, pc = RegistryConfig.model_validate(document).catalogs
    assert mapped_classes("game", 2, ps5) == (
        {"class_values": {"sound_mode": "headset", "light_look": "soft"}},
        False,
    )
    assert mapped_classes("game", 2, pc)[0]["class_values"]["light_look"] == "standard"
    mapped, warning = mapped_classes("game", 4, pc)
    assert warning
    assert write_classes(mapped, {"class_values": {"volume_offset": -5}}, variant=False) == {
        "class_values": {"volume_offset": -5, "sound_mode": "headset", "light_look": "standard"},
    }


async def test_upgraded_media_contracts_keep_primary_and_publish_named_fields(runtime, config):
    import json
    from pathlib import Path

    from core_contracts.extensions.catalog.named_class_migration import projection_changes

    examples = Path(__file__).parents[1] / "examples"
    primary_cases = {
        "media.homepods_class": "media.homepods.track_class",
        "media.ps5_class": "media.ps5.title_class",
        "media.pc_class": "media.pc.title_class",
        "media.appletv_class": "media.appletv.app_class",
        "media.appletv_title_class": "media.appletv.title_class",
    }
    legacy_defs = json.loads(
        (examples / "interface-templates-b22.json").read_text(encoding="utf-8")
    )
    apple = json.loads((examples / "apple-tv-templates-b22.json").read_text(encoding="utf-8"))
    legacy_defs.extend(apple["definitions"])
    document = configured(config)
    document["catalogs"] = json.loads(
        (examples / "interface-catalogs-b22.json").read_text(encoding="utf-8")
    ) + [apple["catalog"]]
    document["definitions"] = [row for row in legacy_defs if row["type_id"] in primary_cases]
    for declaration in document["definitions"]:
        bindings = []
        for input_spec in declaration["inputs"]:
            field = input_spec["schema"]
            name = declaration["type_id"].split(".")[1] + "." + input_spec["name"]
            source_id = "source.synthetic." + name
            document["sources"].append(
                {
                    **deepcopy(document["sources"][0]),
                    "source_id": source_id,
                    "physical_source_key": "synthetic:" + name,
                    "source_type": "boolean"
                    if field["type"] == "bool"
                    else "numeric"
                    if field["type"] == "number"
                    else "text",
                    "value_schema": {
                        key: value
                        for key, value in field.items()
                        if key
                        in {
                            "type",
                            "required",
                            "physical_state",
                            "value_catalog",
                            "unit",
                        }
                    },
                }
            )
            document["bindings"].append(
                {
                    "binding_id": "bind." + name,
                    "source_id": source_id,
                    "adapter": {
                        "kind": "ha_state",
                        "ha_entity_id": "sensor.synthetic_" + name.replace(".", "_"),
                    },
                    **(
                        {"transform": {"kind": "type_cast", "target": field["type"]}}
                        if field["type"] in {"bool", "number"}
                        else {}
                    ),
                }
            )
            bindings.append({"name": input_spec["name"], "ref": source_id})
        document["contracts"].append(
            {
                "contract_id": primary_cases[declaration["type_id"]],
                "type_id": declaration["type_id"],
                "type_version": declaration["type_version"],
                "enabled": True,
                "ha_projection": True,
                "inputs": bindings,
                "parameters": {},
            }
        )
    await activate(runtime, document)
    base = await export(runtime)
    package = build_package(base)
    choices = {key: "file" for key in objects(package["config"])}
    preview = await plan(runtime, package, resolutions=choices)
    assert preview.get("configuration_issues") is None, preview.get("configuration_issues")
    assert not preview["unresolved"]
    inventory = {
        row["contract_id"]: row
        for row in projection_changes(runtime, base["config"], preview["config"])
    }
    assert inventory["media.homepods.track_adjustment"]["primary_field"] == "volume_offset"
    assert inventory["media.homepods.track_adjustment"]["unique_id"].endswith(
        ":media.homepods.track_adjustment"
    )
    assert "media.homepods.track_class" not in inventory
    for contract_id in (
        "media.ps5.title_class",
        "media.pc.title_class",
        "media.appletv.app_class",
        "media.appletv.title_class",
    ):
        assert inventory[contract_id]["primary_field"] == "value"
        assert "volume_offset" in inventory[contract_id]["fields"]


def test_apple_tv_adds_offsets_and_preserves_positive_absence_evidence():
    from types import SimpleNamespace

    from core_contracts.declarative_models import CatalogOffsetSumRule
    from core_contracts.extensions.catalog.catalog_contributions import offset_sum
    from core_contracts.quality import FieldValue, ReasonCode, unknown
    from tests.test_core import NOW

    rule = CatalogOffsetSumRule.model_validate(
        {
            "op": "catalog_offset_sum",
            "application": {"op": "input", "name": "app"},
            "title": {"op": "input", "name": "title"},
        }
    )
    values = {
        "app": FieldValue(status="valid", quality="healthy", value=12, evidence=("app-evidence",)),
        "title": FieldValue(
            status="valid", quality="healthy", value=-4, evidence=("title-evidence",)
        ),
    }
    evaluation = SimpleNamespace(now=NOW, evaluate=lambda argument: values[argument.name])
    valid = offset_sum(evaluation, rule)
    assert valid.value == 8
    assert valid.evidence == ("app-evidence", "title-evidence")
    values["title"] = FieldValue(
        status="not_applicable",
        quality="healthy",
        positively_not_applicable=True,
        evidence=("title-absent",),
    )
    absent = offset_sum(evaluation, rule)
    assert absent.value == 12
    assert absent.evidence == ("app-evidence", "title-absent")
    values["title"] = unknown(ReasonCode.INPUT_UNKNOWN, "title", NOW)
    assert offset_sum(evaluation, rule).status == "unknown"


async def test_mixed_89_type_confirmation_and_shared_rule_preserve_evidence():
    initial = upgrade_document(legacy(), version=5)
    base_sources = deepcopy(initial["sources"])
    initial["sources"] += [
        {
            **deepcopy(base_sources[0]),
            "source_id": f"source.synthetic.{index}",
            "physical_source_key": f"synthetic:{index}",
            "source_type": "text" if index % 4 == 0 else "boolean",
            "value_schema": None,
        }
        for index in range(89 - len(base_sources))
    ]
    runtime = await running(initial)
    try:
        before = runtime.config
        assert len(before.sources) == 89
        observations = deepcopy(runtime.state.tables["source_observation_current"])
        states = deepcopy(runtime.state.tables["contract_state_current"])
        choices = propose_sensor_kinds(before, [])
        assert choices["source.synthetic.0"] == {"sensor_kind": "state_text", "basis": "value_type"}
        assert choices["source.synthetic.1"]["sensor_kind"] is None
        upgraded = storage_document(before)
        upgraded.setdefault("freshness_templates", []).append(
            {
                "template_id": "freshness.synthetic.contact",
                "display_name": "Synthetischer Kontakt",
                "sensor_kind": "contact",
                "rule": {"mode": "report_heartbeat", "interval_s": 10800, "future_tolerance_s": 0},
            }
        )
        for row, source in zip(upgraded["sources"], before.sources, strict=True):
            if source.freshness.interval_s == 10800:
                row.update(sensor_kind="contact", sensor_kind_origin="confirmed")
                row["freshness_selection"] = {
                    "kind": "shared",
                    "template_id": "freshness.synthetic.contact",
                }
        candidate = validate_document(upgraded, runtime.types)
        assert observation_fingerprints(before) == observation_fingerprints(candidate)
        assert assessment_fingerprints(before) == assessment_fingerprints(candidate)
        await activate(runtime, upgraded)
        assert runtime.state.tables["source_observation_current"] == observations
        for key, envelope in states.items():
            after = runtime.state.tables["contract_state_current"][key]
            for name, field in envelope["fields"].items():
                for attr in (
                    "value",
                    "status",
                    "evidence",
                    "since_at",
                    "held_until",
                    "valid_until",
                ):
                    assert after["fields"][name].get(attr) == field.get(attr)
    finally:
        await runtime.stop()


@pytest.mark.postgres
async def test_postgres_named_migration_and_rollback_survive_restart(database, config):
    from tests.test_beta4_postgres import running as postgres_runtime

    async with postgres_runtime(database) as runtime:
        document = configured(config)
        document["catalogs"][0].update(catalog_id="media.homepods", unassigned_value="normal")
        document["catalogs"][0]["classification_schema"]["values"] = [
            "normal",
            "boost",
            "mute",
            "quieter",
        ]
        await activate(runtime, document)
        await runtime.submit(
            "catalog_write",
            {
                "request": {
                    **request(runtime, [change("Synthetic PostgreSQL", classification="boost")]),
                    "catalog_id": "media.homepods",
                }
            },
        )
        package = build_package(await export(runtime))
        decisions = {key: "file" for key in objects(package["config"])}
        preview = await plan(runtime, package, resolutions=decisions)
        staged = await stage(runtime, package, preview, resolutions=decisions)
        await activate_draft(runtime, staged)
    async with postgres_runtime(database) as runtime:
        assert runtime.config.config_schema_version == 5
        catalog = runtime.config.catalogs[0]
        key = identity("example", "music", "title", "Synthetic PostgreSQL")
        assert effective(head(runtime.state, catalog)["entries"], key, catalog, "volume_offset")[
            :2
        ] == (15, True)
        rollback = rollback_preview(runtime.state, runtime.config)
        body = {
            "expected_active_revision": 2,
            "revision": rollback["target_revision"],
            "catalog_migration_revision": rollback["migration_revision"],
            "plan_digest": rollback["plan_digest"],
            "request_id": str(uuid4()),
        }
        restored = await runtime.submit("rollback", body)
    async with postgres_runtime(database) as runtime:
        assert runtime.state.active_revision == 3
        assert await runtime.submit("rollback", body) == restored
        catalog = runtime.config.catalogs[0]
        assert head(runtime.state, catalog)["revision"] == 2
        assert effective(head(runtime.state, catalog)["entries"], key, catalog)[0] == "boost"
