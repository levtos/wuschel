"""English interface migration and projection; synthetic, execution deferred to final b22 gate."""

import copy
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.declarative import check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.extensions.catalog.interface_upgrade import (
    CLASS_CODES,
    contract_id,
    upgrade_package,
)
from core_contracts.extensions.catalog.managed_catalog import head, head_key, identity
from core_contracts.ha_projection import snapshot
from core_contracts.model import checksum
from core_contracts.portable import objects, package_checksum, payload_digest
from core_contracts.runtime import Runtime
from core_contracts.title_sync import current_mapping
from core_contracts.title_transfer import classification
from core_contracts.validation import validate_document as validate
from dev.build_interface_templates import definitions

from .conftest import activate, activate_draft
from .test_beta5_projection import Projection, payload
from .test_declarative import definition, literal, registry
from .test_managed_catalog import configured, request
from .test_portable import export, plan, stage
from .test_title_sync import apply, inspect, mapping, observed


def resign(package):
    version = package["format_version"]
    package["manifest"]["object_digests"] = {
        key: package_checksum(value, version) for key, value in objects(package["config"]).items()
    }
    package["manifest"]["config_digest"] = package_checksum(package["config"], version)
    package["manifest"]["payload_digest"] = payload_digest(package)
    return package


@pytest.mark.parametrize(
    "old,new",
    [
        ("climate.room.synthetic.temperature", "climate.synthetic.temperature"),
        ("climate.thermostat.synthetic", "climate.synthetic.thermostat"),
        ("day.tomorrow.date", "day.date.tomorrow"),
        ("day.sleep_context.today", "day.context.today"),
        ("day.sleep_context.tomorrow", "day.context.tomorrow"),
        ("day.sleep_workday.tomorrow", "day.workday.tomorrow"),
        ("simulation_state", "presence.simulation"),
        ("climate.bath.shower_active", "climate.bath.shower_active"),
    ],
)
def test_approved_contract_id_mapping(old, new):
    assert contract_id(old) == new
    assert contract_id(new) == new


async def test_private_upgrade_renames_refs_removes_duplicate_and_sets_all_public_projections(
    runtime, config
):
    await activate(runtime, registry(config, definition()))
    package = await export(runtime)
    original = copy.deepcopy(package)
    names = [
        "climate.room.synthetic.temperature",
        "climate.thermostat.synthetic",
        "day.tomorrow.date",
        "day.sleep_context.today",
        "day.sleep_context.tomorrow",
        "day.sleep_workday.tomorrow",
        "simulation_state",
        "day.context",
        "media.private_time",
        "sleep.flow",
    ]
    base = package["config"]["contracts"][0]
    package["config"]["contracts"] = [
        {**copy.deepcopy(base), "contract_id": name} for name in names
    ]
    today = next(
        row
        for row in package["config"]["contracts"]
        if row["contract_id"] == "day.sleep_context.today"
    )
    today["type_id"] = "day.context_at"
    next(
        row
        for row in package["config"]["contracts"]
        if row["contract_id"] == "day.sleep_context.tomorrow"
    )["type_id"] = "day.context_at"
    package["config"]["definitions"].append(
        {**copy.deepcopy(package["config"]["definitions"][0]), "type_id": "day.context_at"}
    )
    package["config"]["contracts"][-1]["type_id"] = "sleep.flow"
    package["config"]["definitions"].append(
        {**copy.deepcopy(package["config"]["definitions"][0]), "type_id": "sleep.flow"}
    )
    package["config"]["contracts"][0]["inputs"][0]["ref"] = "day.context.value"
    package = resign(package)
    upgraded = upgrade_package(package)
    rows = {row["contract_id"]: row for row in upgraded["config"]["contracts"]}
    assert "day.context" not in rows and len(rows) == len(names) - 1
    assert rows["day.context.today"]["type_id"] == "day.context_at"
    assert rows["climate.synthetic.temperature"]["inputs"][0]["ref"] == "day.context.today.value"
    assert all(row["ha_projection"] for key, row in rows.items() if key != "sleep.flow")
    assert rows["sleep.flow"]["ha_projection"] is False
    assert rows["media.private_time"]["ha_projection"] is True
    assert not set(names[:-2]).difference({"day.context"}).intersection(rows)
    assert original == await export(runtime)  # No live or active edit while building the file.
    resolutions = {key: "file" for key in objects(upgraded["config"])}
    preview = await plan(runtime, upgraded, resolutions=resolutions)
    draft = await stage(runtime, upgraded, preview, resolutions=resolutions)
    await activate_draft(runtime, draft)
    active = {row.contract_id: row for row in runtime.config.contracts}
    assert set(active) == set(rows) and "day.context" not in active
    assert (
        active["day.context.today"].type_id
        == active["day.context.tomorrow"].type_id
        == "day.context_at"
    )


def test_new_template_versions_validate_and_old_bundles_are_not_replaced():
    root = Path(__file__).parents[1]
    generated = definitions()
    assert (
        json.loads((root / "examples/interface-templates-b22.json").read_text(encoding="utf-8"))
        == generated
    )
    for row in generated:
        check_definition(DeclarativeDefinition.model_validate(row))
    assert not any(row["type_id"] == "day.context" for row in generated)
    assert any(row["type_id"] == "day.context_at" and row["type_version"] == 2 for row in generated)
    assert any(
        row["type_id"] == "media.tv_device"
        and row["schema"]["fields"]["value"]["value_catalog"] == ["ps5", "appletv", "other", "none"]
        for row in generated
    )
    assert any(
        row["type_id"] == "media.lead_device"
        and {"appletv", "other", "none"}.issubset(row["schema"]["fields"]["value"]["value_catalog"])
        for row in generated
    )


def test_day_context_date_specific_template_returns_english_codes():
    from datetime import UTC, datetime

    from core_contracts.declarative import Evaluation

    from .test_beta_sleep_calendars import cache, valid

    now = datetime(2032, 4, 5, 10, tzinfo=UTC)
    data = DeclarativeDefinition.model_validate(
        next(row for row in definitions() if row["type_id"] == "day.context_at")
    )
    holidays = cache(
        now,
        [{"summary": "Synthetic holiday", "start": "2032-04-06", "end": "2032-04-07"}],
        past=0,
        future=31,
    )
    appointments = cache(now, [], past=0, future=31)
    for day, expected in (
        ("2032-04-05", "workday"),
        ("2032-04-06", "day_off"),
        ("2032-04-10", "weekend"),
    ):
        actual = Evaluation(
            data,
            {"date": valid(day), "holidays": holidays, "appointments": appointments},
            now,
            parameters={"vacation_pattern": "*Synthetic*"},
        ).evaluate(data.outputs["value"])
        assert actual.value == expected and actual.quality == "healthy"


async def populated(runtime, config):
    document = configured(registry(config, definition()))
    classes = ["normal", "heimkino", "grind", "headset", "boost", "stumm", "leiser", "privat"]
    document["catalogs"][0]["classification_schema"]["values"] = classes
    document["catalogs"][0]["unassigned_value"] = "normal"
    await activate(runtime, document)
    changes = [
        {
            "scope": "example",
            "media_type": "music",
            "signal_type": "title",
            "key": f"Synthetic {index}",
            "classification": value,
            "display_name": f"Synthetic {index}",
            "note": "Synthetic note",
        }
        for index, value in enumerate(classes)
    ]
    changes.append(
        {
            **changes[1],
            "key": "Synthetic Variant",
            "classification": None,
            "master_key": "Synthetic 1",
        }
    )
    await runtime.submit("catalog_write", {"request": request(runtime, changes)})
    catalog = runtime.config.catalogs[0]
    state = runtime.state.clone()
    key = identity("example", "music", "title", "Synthetic 1")
    state.tables["catalog_sighting"][head_key(catalog) + ":" + key] = {
        "catalog": head_key(catalog),
        "identity": key,
        "scope": "example",
        "media_type": "music",
        "signal_type": "title",
        "key": "Synthetic 1",
        "first_seen": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T01:00:00Z",
        "seen_count": 7,
    }
    # Existing saved TC target must move in the same activation transaction.
    state.tables["title_sync_mapping"]["1"] = {
        "version": 1,
        "mapping": mapping(),
        "at": runtime.clock.now_utc().isoformat(),
    }
    state.tables["catalog_sighting"][head_key(catalog) + ":" + key]["source"] = "synthetic"
    state.tables["service_state"][
        "catalog_playback:"
        + checksum([head_key(catalog), "example", "music", "title", "synthetic"])
    ] = {
        "source": "synthetic",
        "last": key,
        "last_raw": "Synthetic 1",
        "last_session": 0,
        "report": "synthetic",
    }
    await runtime._commit(state)
    return catalog


async def upgrade_plan(runtime):
    package = upgrade_package(await export(runtime))
    resolutions = {key: "file" for key in objects(package["config"])}
    preview = await plan(runtime, package, resolutions=resolutions)
    return package, resolutions, preview


async def test_all_class_assignments_masters_sightings_and_tc_survive_atomic_upgrade_and_restart(
    runtime, config
):
    old_catalog = await populated(runtime, config)
    tc_rows = [observed("Synthetic Imported", enum=2)]
    initial_sync = await inspect(runtime, tc_rows)
    assert initial_sync["can_apply"]
    await apply(runtime, initial_sync)
    assert any(
        row["classification"] == "stumm" and row.get("tc_metadata")
        for row in head(runtime.state, old_catalog)["entries"].values()
    )
    original = copy.deepcopy(head(runtime.state, old_catalog))
    package, resolutions, preview = await upgrade_plan(runtime)
    assert preview["unresolved"] == []
    assert preview["catalog_migrations"][0]["entries"] == len(original["entries"])
    draft = await stage(runtime, package, preview, resolutions=resolutions)
    assert runtime.config.catalogs[0].version == 1
    await activate_draft(runtime, draft)
    new = runtime.config.catalogs[0]
    after = head(runtime.state, new)["entries"]
    assert new.version == 2 and set(after) == set(original["entries"])
    assert Counter(row["classification"] for row in after.values()) == Counter(
        CLASS_CODES.get(row["classification"], row["classification"])
        for row in original["entries"].values()
    )
    assert all(row["classification"] not in CLASS_CODES for row in after.values())
    for key, row in original["entries"].items():
        assert after[key]["master_key"] == row["master_key"]
        assert after[key]["note"] == row["note"]
    sightings = list(runtime.state.tables["catalog_sighting"].values())
    assert (
        len(sightings) == 1
        and sightings[0]["catalog"] == head_key(new)
        and sightings[0]["seen_count"] == 7
    )
    tracker = runtime.state.tables["service_state"][
        "catalog_playback:" + checksum([head_key(new), "example", "music", "title", "synthetic"])
    ]
    assert tracker["last_raw"] == "Synthetic 1" and tracker["last_session"] == 0
    assert (
        current_mapping(runtime.state)["mapping"]["mappings"][next(iter(mapping()["mappings"]))][
            "definition_version"
        ]
        == 2
    )
    # A real TC run after migration uses the new codes, not v1's German classes.
    sync = await inspect(runtime, tc_rows)
    assert sync["can_apply"], sync["errors"]
    await apply(runtime, sync)
    assert any(
        row["classification"] == "mute" for row in head(runtime.state, new)["entries"].values()
    )
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        assert restored.config.catalogs[0].version == 2
        assert (
            sum(row["seen_count"] for row in restored.state.tables["catalog_sighting"].values())
            == 7
        )
    finally:
        await restored.stop()


@pytest.mark.parametrize("mode", ["missing", "changed", "raced"])
async def test_incomplete_or_stale_upgrade_is_rejected_without_any_partial_change(
    runtime, config, mode
):
    catalog = await populated(runtime, config)
    package, resolutions, preview = await upgrade_plan(runtime)
    if mode == "raced":
        await runtime.submit(
            "catalog_write",
            {
                "request": request(
                    runtime,
                    [
                        {
                            "scope": "example",
                            "media_type": "music",
                            "signal_type": "title",
                            "key": "Synthetic Late",
                            "classification": "grind",
                        }
                    ],
                    revision=head(runtime.state, catalog)["revision"],
                )
            },
        )
    else:
        entries = package["catalog_contents"][0]["entries"]
        if mode == "missing":
            entries.pop()
        else:
            entries[0]["classification"] = (
                "grind" if entries[0]["classification"] != "grind" else "normal"
            )
        resign(package)
    before = runtime.state.clone()
    with pytest.raises(ValueError, match="interface_upgrade_(invalid|conflict)"):
        await plan(runtime, package, resolutions=resolutions)
    assert runtime.state.tables == before.tables


@pytest.mark.parametrize(
    "profile,value,wanted",
    [("game", 0, "home_cinema"), ("music", 2, "mute"), ("private", 1, "private")],
)
def test_tc_classification_is_version_aware(profile, value, wanted):
    from types import SimpleNamespace

    assert classification(profile, value, SimpleNamespace(version=2)) == wanted
    assert classification(profile, value, SimpleNamespace(version=1)) != wanted


async def test_multifield_public_sensor_has_all_field_metadata_and_internal_is_excluded(
    runtime, config
):
    data = definition()
    data["schema"]["fields"]["extra"] = {"type": "text", "required": True, "physical_state": False}
    data["outputs"]["extra"] = literal("Synthetic", "text")
    document = registry(config, data)
    document["contracts"][0]["ha_projection"] = True
    document["contracts"].append(
        {
            **copy.deepcopy(document["contracts"][0]),
            "contract_id": "fixture.internal",
            "ha_projection": False,
        }
    )
    validate(document, runtime.types)
    await activate(runtime, document)
    runtime.installation_id = str(uuid4())
    projected = snapshot(runtime)
    assert len(projected["contracts"]) == 1
    row = projected["contracts"][0]
    assert row["primary_field"] == "value" and row["fields"]["extra"]["value"] == "Synthetic"
    assert set(row["fields"]["extra"]) == {"value", "status", "quality", "since_at", "unit"}
    projection = Projection()
    projection.accept(object(), runtime.installation_id, projected)
    assert projection.rows[row["contract_id"]]["fields"]["extra"]["value"] == "Synthetic"
    runtime.confirmed = False
    assert all(
        field["value"] is None and field["status"] == "unknown"
        for field in snapshot(runtime)["contracts"][0]["fields"].values()
    )


def test_projection_rejects_stale_nested_values_atomically():
    data = payload()
    row = data["contracts"][0]
    row.update(
        primary_field="value",
        fields={
            "value": {key: row[key] for key in ("value", "status", "quality", "since_at", "unit")}
        },
    )
    projection, owner = Projection(), object()
    projection.accept(owner, data["installation_id"], data)
    previous = copy.deepcopy(projection.rows)
    invalid = copy.deepcopy(data)
    invalid["contracts"][0]["fields"]["extra"] = {
        "value": "Synthetic Old",
        "status": "unknown",
        "quality": "degraded",
        "since_at": None,
        "unit": None,
    }
    with pytest.raises(ValueError, match="projection_value_invalid"):
        projection.accept(owner, data["installation_id"], invalid)
    assert projection.rows == previous


async def test_multifield_primary_can_use_a_named_field_without_value(runtime, config):
    data = definition()
    data["schema"]["fields"] = {
        "state": {"type": "bool", "required": True, "physical_state": False},
        "detail": {"type": "text", "required": True, "physical_state": False},
    }
    data["schema"]["available_projection"] = ["state"]
    data["outputs"] = {"state": literal(True), "detail": literal("Synthetic", "text")}
    document = registry(config, data)
    document["contracts"][0]["ha_projection"] = True
    await activate(runtime, document)
    row = snapshot(runtime)["contracts"][0]
    assert row["primary_field"] == "state" and row["value"] is True
    assert row["fields"]["detail"]["value"] == "Synthetic"


@pytest.mark.parametrize("mode", ["race", "commit"])
async def test_activation_rechecks_source_and_rolls_back_entire_migration(runtime, config, mode):
    catalog = await populated(runtime, config)
    package, resolutions, preview = await upgrade_plan(runtime)
    draft = await stage(runtime, package, preview, resolutions=resolutions)
    if mode == "race":
        await runtime.submit(
            "catalog_write",
            {
                "request": request(
                    runtime,
                    [
                        {
                            "scope": "example",
                            "media_type": "music",
                            "signal_type": "title",
                            "key": "Synthetic Concurrent",
                            "classification": "grind",
                        }
                    ],
                    revision=head(runtime.state, catalog)["revision"],
                )
            },
        )
    before = runtime.state.clone()

    def fail():
        raise RuntimeError("synthetic interface migration failure")

    if mode == "commit":
        runtime.store.before_commit = fail
    try:
        with pytest.raises(
            (ValueError, RuntimeError),
            match="interface_upgrade_conflict|synthetic interface migration failure",
        ):
            await activate_draft(runtime, draft)
    finally:
        runtime.store.before_commit = None
    assert runtime.state.tables == before.tables
    assert runtime.config.catalogs[0].version == 1
    assert (
        current_mapping(runtime.state)["mapping"]["mappings"][next(iter(mapping()["mappings"]))][
            "definition_version"
        ]
        == 1
    )
