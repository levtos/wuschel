from uuid import uuid4

import pytest

from core_contracts.extensions.catalog.catalog_care import all_rows, detail, listing, queue
from core_contracts.extensions.catalog.managed_catalog import head

from .conftest import activate
from .test_beta_media_import import document
from .test_title_sync import apply, inspect, mapping, observed


async def seeded(runtime, config, rows):
    await activate(runtime, document(config))
    plan = await inspect(runtime, rows, mapping())
    assert plan["can_apply"], plan["errors"]
    await apply(runtime, plan)
    return runtime.config.catalogs[0]


def care(runtime, identities, action, **extra):
    return {
        "catalog_id": "example.titles",
        "definition_version": 1,
        "expected_active_revision": runtime.state.active_revision,
        "expected_content_revision": head(runtime.state, runtime.config.catalogs[0])["revision"],
        "request_id": str(uuid4()),
        "reason": "Synthetic catalog care",
        "identities": identities,
        "action": action,
        **extra,
    }


async def test_promote_swaps_the_whole_family_and_preserves_class(runtime, config):
    catalog = await seeded(
        runtime,
        config,
        [observed("Synthetic Original"), observed("Synthetic Remix"), observed("Synthetic Edit")],
    )
    rows = list(all_rows(runtime.state, catalog).values())
    ids = [row["identity"] for row in rows]
    await runtime.submit(
        "catalog_care_write",
        {"request": care(runtime, ids, "group", master=ids[0], classification="boost")},
    )
    payload = care(runtime, [ids[1]], "promote")
    before = head(runtime.state, catalog)["revision"]
    await runtime.submit("catalog_care_preview", {"request": payload})
    assert head(runtime.state, catalog)["revision"] == before
    result = await runtime.submit("catalog_care_write", {"request": payload})
    assert await runtime.submit("catalog_care_write", {"request": payload}) == result
    family = all_rows(runtime.state, catalog)
    assert family[ids[1]]["master_identity"] is None
    assert all(family[key]["master_identity"] == ids[1] for key in (ids[0], ids[2]))
    assert all(row["effective_classification"] == "boost" for row in family.values())


async def test_class_filter_keeps_family_and_master_view_omits_singles(runtime, config):
    catalog = await seeded(
        runtime,
        config,
        [observed("Synthetic Root"), observed("Synthetic Child"), observed("Synthetic Solo")],
    )
    ids = list(all_rows(runtime.state, catalog))
    await runtime.submit(
        "catalog_care_write",
        {"request": care(runtime, ids[:2], "group", master=ids[0], classification="boost")},
    )
    await runtime.submit(
        "catalog_care_write",
        {"request": care(runtime, [ids[2]], "classify", classification="normal")},
    )
    page = listing(runtime.state, catalog, grouped=True, filters={"classification": "boost"})
    assert len(page["items"]) == 1 and page["items"][0]["identity"] == ids[0]
    assert page["items"][0]["variant_count"] == 1
    assert listing(runtime.state, catalog, grouped=True, view="masters")["total"] == 1
    assert not listing(runtime.state, catalog, grouped=True, filters={"classification": "missing"})[
        "items"
    ]


async def test_all_catalog_cursor_is_server_side_and_bound_to_filter_and_revision(runtime, config):
    from core_contracts.extensions.catalog.catalog_views import (
        export_structure,
        inspect_structure,
        page_all,
    )

    from .test_managed_catalog import catalog as definition
    from .test_managed_catalog import change, configured, request

    document = configured(config)
    document["catalogs"].append({**definition(), "catalog_id": "example.second"})
    await activate(runtime, document)
    await runtime.submit(
        "catalog_write",
        {"request": request(runtime, [change(f"Synthetic {i:03}") for i in range(100)])},
    )
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                **request(runtime, [change("Synthetic Other")]),
                "catalog_id": "example.second",
            }
        },
    )
    before = runtime.state.model_dump()
    first = page_all(runtime.state, runtime.config, sort="title")
    assert len(first["items"]) == 100 and first["total"] == 101
    second = page_all(runtime.state, runtime.config, sort="title", after=first["next_cursor"])
    assert len(second["items"]) == 1 and not second["next_cursor"]
    assert {row["catalog_id"] for row in first["items"] + second["items"]} == {
        "example.titles",
        "example.second",
    }
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        page_all(
            runtime.state, runtime.config, sort="title", query="Other", after=first["next_cursor"]
        )
    assert (
        page_all(runtime.state, runtime.config, classification_catalog="example.second@1")["total"]
        == 1
    )
    csv = export_structure(runtime.state, runtime.config, "all", 1)
    assert csv.startswith("\ufeffKatalog;Version;") and "Synthetic Other" in csv
    assert inspect_structure(runtime.state, runtime.config, "all", 1)["counts"] == {
        "missing_artist": 101
    }
    assert runtime.state.model_dump() == before


async def test_new_music_version_uses_a_base_work_and_late_original_is_not_duplicated(
    runtime, config
):
    from core_contracts.extensions.catalog.managed_catalog import discovered_entries
    from core_contracts.model import canonical

    from .test_beta_media import configuration, observe
    from .test_catalog_playback import advance

    await activate(runtime, configuration(config, "media.homepods_class"))
    await observe(runtime, "applicable", True)
    await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Work (Remix)")
    await advance(runtime, 2)
    catalog = runtime.config.catalogs[0]
    rows = all_rows(runtime.state, catalog)
    assert len(rows) == 2
    root = next(row for row in rows.values() if not row.get("master_identity"))
    child = next(row for row in rows.values() if row.get("master_identity"))
    assert root["key"] == canonical(["Synthetic Artist", "Synthetic Work"])
    assert root["family_seen_count"] == 1 and child["seen_count"] == 1
    await observe(runtime, "title", "Synthetic Work")
    await advance(runtime, 12)
    rows = all_rows(runtime.state, catalog)
    assert len(rows) == 2 and rows[root["identity"]]["family_seen_count"] == 2
    assert root["identity"] in discovered_entries(runtime.state, catalog)


async def test_structure_findings_and_excel_export_are_read_only(runtime, config):
    from core_contracts.extensions.catalog.catalog_views import export_structure, inspect_structure

    from .test_managed_catalog import change, configured, request

    await activate(runtime, configured(config))
    root = "Synthetic A & Synthetic B - Synthetic Work (Remix)"
    await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime,
                [
                    change(root),
                    change(
                        "Synthetic A & Synthetic B - Synthetic Other",
                        master_key=root,
                        classification=None,
                    ),
                    change("Synthetic A & Synthetic B - Synthetic Work (Edit)"),
                    change("Synthetic B & Synthetic A - Synthetic Work"),
                    change("Missing artist"),
                    change("=1+1"),
                ],
            )
        },
    )
    before = runtime.state.model_dump()
    findings = inspect_structure(runtime.state, runtime.config, "all", 1)
    assert set(findings["counts"]) >= {
        "version_master",
        "different_work",
        "split_family",
        "missing_artist",
        "artist_spelling",
    }
    assert export_structure(runtime.state, runtime.config, "all", 1).find(";'=1+1;") >= 0
    assert runtime.state.model_dump() == before


async def test_structure_check_respects_studio_and_series_namespaces(runtime, config):
    from core_contracts.extensions.catalog.catalog_views import inspect_structure

    from .test_managed_catalog import change, configured, request

    await activate(runtime, configured(config))
    await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime,
                [
                    change("studio:synthetic", media_type="video", signal_type="scene"),
                    change(
                        "synthetic-scene",
                        media_type="video",
                        signal_type="scene",
                        master_key="studio:synthetic",
                        classification=None,
                    ),
                    change("master:synthetic", media_type="video"),
                    change(
                        "item:synthetic",
                        media_type="video",
                        master_key="master:synthetic",
                        classification=None,
                    ),
                ],
            )
        },
    )
    assert inspect_structure(runtime.state, runtime.config, "all", 1)["total"] == 0


async def test_music_inherits_base_class_but_keeps_an_explicit_variant_override(runtime, config):
    from core_contracts.model import canonical

    from .test_beta_media import configuration, field, observe
    from .test_catalog_playback import advance
    from .test_managed_catalog import request

    await activate(runtime, configuration(config, "media.homepods_class"))
    catalog = runtime.config.catalogs[0]
    base = canonical(["Synthetic Artist", "Synthetic Work"])
    remix = canonical(["Synthetic Artist", "Synthetic Work (Remix)"])
    body = {
        **request(
            runtime,
            [
                {
                    "scope": "homepods",
                    "media_type": "music",
                    "signal_type": "title",
                    "key": base,
                    "classification": "boost",
                }
            ],
        ),
        "catalog_id": catalog.catalog_id,
        "definition_version": catalog.version,
    }
    await runtime.submit("catalog_write", {"request": body})
    await observe(runtime, "applicable", True)
    await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Work (Remix)")
    assert field(runtime)["value"] == "boost"
    await advance(runtime, 2)
    # A manual standalone correction wins over automatic grouping on later reports.
    body.update(
        request_id=str(uuid4()),
        expected_content_revision=1,
        changes=[
            {
                "scope": "homepods",
                "media_type": "music",
                "signal_type": "title",
                "key": remix,
                "classification": "normal",
                "master_key": None,
            }
        ],
    )
    await runtime.submit("catalog_write", {"request": body})
    await observe(runtime, "title", "Synthetic Work (Remix)", kind="report")
    assert field(runtime)["value"] == "normal"


async def test_status_and_sort_paging_count_source_once_and_never_reset_context(
    runtime, config, monkeypatch
):
    catalog = await seeded(runtime, config, [observed(f"Synthetic {n:03}") for n in range(102)])
    first = listing(runtime.state, catalog, sort="title", view="open")
    assert len(first["items"]) == 100 and first["total"] == 102
    second = listing(runtime.state, catalog, sort="title", view="open", after=first["next_cursor"])
    assert len(second["items"]) == 2 and not second["next_cursor"]
    selected = [row["identity"] for row in first["items"][:2]]
    original_sequence = runtime.state.publication_seq

    def no_evaluation(*args, **kwargs):
        pytest.fail("metadata-only care evaluated contracts")

    monkeypatch.setattr(runtime, "_evaluate", no_evaluation)
    payload = care(runtime, selected, "hide")
    await runtime.submit("catalog_care_preview", {"request": payload})
    await runtime.submit("catalog_care_write", {"request": payload})
    assert listing(runtime.state, catalog, view="hidden")["total"] == 2
    assert runtime.state.publication_seq == original_sequence
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        listing(runtime.state, catalog, sort="title", view="open", after=first["next_cursor"])
    await runtime.submit("catalog_care_write", {"request": care(runtime, selected, "review")})
    await runtime.submit("catalog_care_write", {"request": care(runtime, selected, "unhide")})
    assert listing(runtime.state, catalog, view="reviewed")["total"] == 2


async def test_bulk_group_is_atomic_flattens_runtime_aliases_and_preserves_details(runtime, config):
    catalog = await seeded(
        runtime, config, [observed("Synthetic Song (Remix)"), observed("Synthetic Song (Edit)")]
    )
    candidates = queue(runtime.state, catalog, "", "all", {})
    assert len(candidates["groups"]) == 1
    members = candidates["groups"][0]["members"]
    ids = [row["identity"] for row in members]
    master = candidates["groups"][0]["master"]
    payload = care(runtime, ids, "group", master=master, classification="boost")
    before = head(runtime.state, catalog)["revision"]
    preview = await runtime.submit("catalog_care_preview", {"request": payload})
    assert head(runtime.state, catalog)["revision"] == before
    assert "context_effects" in preview
    saved = await runtime.submit("catalog_care_write", {"request": payload})
    assert saved["content_revision"] == before + 1
    assert await runtime.submit("catalog_care_write", {"request": payload}) == saved
    details = detail(runtime.state, catalog, master)
    assert len(details["variants"]) == 1
    assert details["variants"][0]["effective_classification"] == "boost"
    assert details["variants"][0]["tc_metadata"]["contexts"]
    entries = head(runtime.state, catalog)["entries"]
    assert all(
        not entries[row["master_identity"]].get("master_identity")
        for row in entries.values()
        if row.get("master_identity")
    )
    child = details["variants"][0]["identity"]
    await runtime.submit(
        "catalog_care_write", {"request": care(runtime, [child], "ungroup", classification="stumm")}
    )
    assert all_rows(runtime.state, catalog)[child]["effective_classification"] == "stumm"
    child_source = head(runtime.state, catalog)["entries"][child]["tc_metadata"]["source_identity"]
    from core_contracts.extensions.catalog.managed_catalog import effective

    stored = head(runtime.state, catalog)["entries"]
    child_aliases = [
        key
        for key, row in stored.items()
        if row.get("tc_metadata", {}).get("is_alias")
        and row["tc_metadata"]["source_identity"] == child_source
    ]
    assert len(child_aliases) == 1
    assert effective(stored, child_aliases[0], catalog)[0] == "stumm"


async def test_care_conflicts_and_variants_cannot_be_directly_reclassified(runtime, config):
    catalog = await seeded(
        runtime, config, [observed("Synthetic Song (one)"), observed("Synthetic Song (two)")]
    )
    ids = list(all_rows(runtime.state, catalog))
    stale = care(runtime, ids, "review")
    await runtime.submit("catalog_care_write", {"request": care(runtime, ids, "hide")})
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await runtime.submit("catalog_care_write", {"request": stale})
    await runtime.submit(
        "catalog_care_write", {"request": care(runtime, ids, "group", master=ids[0])}
    )
    with pytest.raises(ValueError, match="catalog_variant_override"):
        await runtime.submit(
            "catalog_care_write",
            {"request": care(runtime, [ids[1]], "classify", classification="stumm")},
        )
