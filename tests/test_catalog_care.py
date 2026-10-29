from uuid import uuid4

import pytest

from core_contracts.catalog_care import all_rows, detail, listing, queue
from core_contracts.managed_catalog import head

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
        runtime, config, [observed("Synthetic Song (one)"), observed("Synthetic Song (two)")]
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
    from core_contracts.managed_catalog import effective

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
