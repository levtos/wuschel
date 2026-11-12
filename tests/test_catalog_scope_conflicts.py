"""PR94 review: conflicts are planned per entry, never a whole-plan failure."""

import copy

import pytest

from core_contracts.extensions.catalog.managed_catalog import head, head_key, identity

from .test_catalog_scope import entry, preview, repair, setup, write


def target_key(key):
    return identity("homepods", "music", "title", key)


def finding(plan, reason, key="Synthetic Variant"):
    return next(
        row
        for catalog in plan["catalogs"]
        for row in catalog["findings"]
        if row["reason"] == reason and row["key"] == key
    )


async def test_live_master_wins_and_preview_matches_the_committed_resolution(runtime, config):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master A"),
            entry("Synthetic Variant", classification=None, master_key="Synthetic Master A"),
            entry("Synthetic Master B", scope="homepods", classification="normal"),
            entry(
                "Synthetic Variant",
                scope="homepods",
                classification=None,
                master_key="Synthetic Master B",
            ),
        ],
    )
    before = runtime.state.model_dump()
    plan = await preview(runtime)
    assert runtime.state.model_dump() == before
    row = finding(plan, "live_master")
    assert row["master_before"] == "Synthetic Master A"
    assert row["master_after"] == "Synthetic Master B"
    assert plan["catalogs"][0]["conflicts"] == 1
    result = await repair(runtime, plan)
    assert result["catalogs"] == plan["catalogs"]
    entries = head(runtime.state, catalog)["entries"]
    child = entries[target_key("Synthetic Variant")]
    assert child["master_identity"] == target_key("Synthetic Master B")
    assert entries[target_key("Synthetic Master B")]["classification"] == "normal"
    after = copy.deepcopy(runtime.state.model_dump())
    assert await repair(runtime) == result
    assert runtime.state.model_dump() == after


async def test_conflicting_live_class_remains_standalone_without_overwriting_master(
    runtime, config
):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master"),
            entry("Synthetic Variant", classification=None, master_key="Synthetic Master"),
            entry("Synthetic Master", scope="homepods", classification="stumm"),
            entry("Synthetic Variant", scope="homepods", classification="normal"),
        ],
    )
    plan = await preview(runtime)
    assert finding(plan, "live_class_standalone")["master_after"] is None
    await repair(runtime, plan)
    entries = head(runtime.state, catalog)["entries"]
    assert entries[target_key("Synthetic Variant")]["master_key"] is None
    assert entries[target_key("Synthetic Variant")]["classification"] == "normal"
    assert entries[target_key("Synthetic Master")]["classification"] == "stumm"


async def test_missing_master_is_planned_as_standalone_unassigned_and_other_entries_move(
    runtime, config
):
    catalog = await setup(runtime, config)
    await write(runtime, catalog, [entry("Synthetic Safe")])
    key = identity("default", "music", "title", "Synthetic Orphan")
    runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + key] = {
        "catalog": head_key(catalog),
        "identity": key,
        **entry("Synthetic Orphan", classification=None, master_key="Synthetic Missing"),
        "seen_count": 1,
        "first_seen": "2032-01-01T00:00:00Z",
        "last_seen": "2032-01-01T00:00:00Z",
    }
    plan = await preview(runtime)
    assert finding(plan, "missing_master_standalone", "Synthetic Orphan")["master_after"] is None
    await repair(runtime, plan)
    entries = head(runtime.state, catalog)["entries"]
    assert entries[target_key("Synthetic Orphan")]["master_key"] is None
    assert entries[target_key("Synthetic Orphan")]["classification"] is None
    assert entries[target_key("Synthetic Safe")]["classification"] == "boost"
    assert all(
        not row.get("master_identity") or not entries[row["master_identity"]].get("master_identity")
        for row in entries.values()
        if not row["tombstone"]
    )


async def test_different_live_sibling_classes_stay_independent(runtime, config):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master"),
            entry("Synthetic Child A", classification=None, master_key="Synthetic Master"),
            entry("Synthetic Child B", classification=None, master_key="Synthetic Master"),
            entry("Synthetic Child A", scope="homepods", classification="normal"),
            entry("Synthetic Child B", scope="homepods", classification="stumm"),
        ],
    )
    plan = await preview(runtime)
    assert plan["catalogs"][0]["conflicts"] == 2
    await repair(runtime, plan)
    entries = head(runtime.state, catalog)["entries"]
    for key, value in [("Synthetic Child A", "normal"), ("Synthetic Child B", "stumm")]:
        assert entries[target_key(key)]["master_key"] is None
        assert entries[target_key(key)]["classification"] == value
    assert entries[target_key("Synthetic Master")]["classification"] == "boost"


async def test_imported_family_follows_a_preserved_live_master_without_grandchildren(
    runtime, config
):
    catalog = await setup(runtime, config)
    await write(
        runtime,
        catalog,
        [
            entry("Synthetic Master A"),
            entry("Synthetic Variant", classification=None, master_key="Synthetic Master A"),
            entry("Synthetic Master B", scope="homepods", classification="normal"),
            entry(
                "Synthetic Master A",
                scope="homepods",
                classification=None,
                master_key="Synthetic Master B",
            ),
        ],
    )
    plan = await preview(runtime)
    assert finding(plan, "master_flattened")["master_after"] == "Synthetic Master B"
    await repair(runtime, plan)
    entries = head(runtime.state, catalog)["entries"]
    assert entries[target_key("Synthetic Variant")]["master_identity"] == target_key(
        "Synthetic Master B"
    )
    assert entries[target_key("Synthetic Master A")]["master_identity"] == target_key(
        "Synthetic Master B"
    )


async def test_apply_rejects_a_different_resolution_even_with_the_same_source_basis(
    runtime, config, monkeypatch
):
    from core_contracts.extensions.catalog import catalog_scope

    catalog = await setup(runtime, config)
    await write(runtime, catalog, [entry()])
    plan = await preview(runtime)
    original = catalog_scope.build

    def different(*args):
        result = original(*args)
        result["groups"][0]["entries"][-1]["note"] = "Synthetic changed plan"
        return result

    before = runtime.state.model_dump()
    monkeypatch.setattr(catalog_scope, "build", different)
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await repair(runtime, plan)
    assert runtime.state.model_dump() == before
