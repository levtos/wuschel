"""P5 required cases; synthetic only, execution deferred until the last b22 package."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.catalog_care import CareRequest, all_rows, detail, listing
from core_contracts.managed_catalog import effective, head, identity
from core_contracts.model import checksum

from .conftest import activate
from .test_catalog_care import care, seeded
from .test_title_sync import observed

CATALOGS = json.loads(
    (Path(__file__).parents[1] / "examples/interface-catalogs-b22.json").read_text(encoding="utf-8")
)


async def seed_catalog(runtime, config, catalog):
    doc = copy.deepcopy(config)
    doc.update(config_schema_version=3, fingerprint_version=3, catalogs=[catalog])
    await activate(runtime, doc)
    changes = [
        {
            "scope": "synthetic",
            "media_type": "synthetic",
            "signal_type": "synthetic",
            "key": "Synthetic item " + str(index),
            "classification": catalog["classification_schema"]["values"][0],
        }
        for index in (1, 2)
    ]
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": catalog["catalog_id"],
                "definition_version": catalog["version"],
                "expected_active_revision": runtime.state.active_revision,
                "expected_content_revision": 0,
                "request_id": str(uuid4()),
                "reason": "Synthetic seed",
                "changes": changes,
            }
        },
    )
    return runtime.config.catalogs[0]


def request(runtime, catalog, ids, **extra):
    return {
        "catalog_id": catalog.catalog_id,
        "definition_version": catalog.version,
        "expected_active_revision": runtime.state.active_revision,
        "expected_content_revision": head(runtime.state, catalog)["revision"],
        "request_id": str(uuid4()),
        "reason": "Synthetic grouping",
        "action": "group",
        "identities": ids,
        "new_master_name": "Synthetic free master",
        "classification": catalog.classification_schema.values[-1],
        **extra,
    }


@pytest.mark.parametrize("catalog", CATALOGS, ids=lambda row: row["catalog_id"])
async def test_free_master_in_every_catalog_is_atomic_and_variants_inherit(
    runtime, config, catalog
):
    definition = await seed_catalog(runtime, config, catalog)
    ids = list(all_rows(runtime.state, definition))
    payload = request(runtime, definition, ids)
    before = copy.deepcopy(runtime.state.tables)
    preview = await runtime.submit("catalog_care_preview", {"request": payload})
    assert len(preview["changes"]) == 3
    assert runtime.state.tables == before
    saved = await runtime.submit("catalog_care_write", {"request": payload})
    assert saved["content_revision"] == 2
    assert await runtime.submit("catalog_care_write", {"request": payload}) == saved
    current = head(runtime.state, definition)
    master = identity("synthetic", "synthetic", "synthetic", payload["new_master_name"])
    assert current["entries"][master]["classification"] == payload["classification"]
    assert not current["entries"][master].get("master_key")
    assert master not in before["catalog_head"][next(iter(before["catalog_head"]))]["entries"]
    for key in ids:
        assert current["entries"][key]["classification"] is None
        assert effective(current["entries"], key, definition)[:2] == (
            payload["classification"],
            True,
        )
        assert current["entries"][key]["master_identity"] == master
    page = listing(runtime.state, definition, grouped=True)
    assert [row["identity"] for row in page["items"]] == [master]
    assert page["total"] == 1 and page["matching_entries"] == 3
    assert len(detail(runtime.state, definition, master)["variants"]) == 2


async def test_named_master_group_moves_tc_aliases_and_keeps_metadata_and_counts(runtime, config):
    catalog = await seeded(runtime, config, [observed("Synthetic One"), observed("Synthetic Two")])
    before = copy.deepcopy(head(runtime.state, catalog)["entries"])
    sightings = copy.deepcopy(runtime.state.tables["catalog_sighting"])
    ids = list(all_rows(runtime.state, catalog))
    payload = care(
        runtime, ids, "group", new_master_name="Synthetic umbrella", classification="boost"
    )
    await runtime.submit("catalog_care_write", {"request": payload})
    after = head(runtime.state, catalog)["entries"]
    for key, row in before.items():
        assert after[key]["tc_metadata"] == row["tc_metadata"]
        assert after[key]["classification"] is None
        assert after[key]["master_key"] == "Synthetic umbrella"
        assert effective(after, key, catalog)[0] == "boost"
    assert runtime.state.tables["catalog_sighting"] == sightings


@pytest.mark.parametrize(
    "case",
    ["name_collision", "both_targets", "blank", "missing_class", "mixed_scope", "nested_master"],
)
async def test_invalid_new_master_requests_leave_no_partial_master(runtime, config, case):
    catalog = await seed_catalog(runtime, config, CATALOGS[0])
    ids = list(all_rows(runtime.state, catalog))
    extra = {}
    if case == "name_collision":
        extra["new_master_name"] = "  SYNTHETIC ITEM 1  "
    elif case == "both_targets":
        extra["master"] = ids[0]
    elif case == "blank":
        extra["new_master_name"] = "   "
    elif case == "missing_class":
        extra["classification"] = None
    elif case == "mixed_scope":
        await runtime.submit(
            "catalog_write",
            {
                "request": {
                    "catalog_id": catalog.catalog_id,
                    "definition_version": catalog.version,
                    "expected_active_revision": runtime.state.active_revision,
                    "expected_content_revision": 1,
                    "request_id": str(uuid4()),
                    "reason": "Synthetic other scope",
                    "changes": [
                        {
                            "scope": "other",
                            "media_type": "synthetic",
                            "signal_type": "synthetic",
                            "key": "Synthetic other",
                            "classification": catalog.unassigned_value,
                        }
                    ],
                }
            },
        )
        ids = list(all_rows(runtime.state, catalog))
    else:
        await runtime.submit(
            "catalog_care_write",
            {
                "request": {
                    **request(runtime, catalog, ids),
                    "new_master_name": None,
                    "master": ids[0],
                }
            },
        )
        ids = [ids[0]]
    before = copy.deepcopy(runtime.state.tables)
    with pytest.raises(ValueError):
        await runtime.submit(
            "catalog_care_write", {"request": request(runtime, catalog, ids, **extra)}
        )
    assert runtime.state.tables == before


async def test_named_master_commit_failure_and_stale_preview_preserve_state(runtime, config):
    catalog = await seed_catalog(runtime, config, CATALOGS[0])
    payload = request(runtime, catalog, list(all_rows(runtime.state, catalog)))
    before = runtime.state
    tables = copy.deepcopy(before.tables)

    def fail():
        raise RuntimeError("synthetic commit failure")

    runtime.store.before_commit = fail
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            await runtime.submit("catalog_care_write", {"request": payload})
    finally:
        runtime.store.before_commit = None
    assert runtime.state is before and runtime.state.tables == tables
    stored = await runtime.store.load()
    assert stored.tables["catalog_head"] == tables["catalog_head"]
    assert stored.tables["contract_state_current"] == tables["contract_state_current"]
    assert await runtime.store.get("catalog_request", payload["request_id"]) is None
    await runtime.submit("recover", {})
    await runtime.submit("catalog_care_write", {"request": payload})
    stale = {**payload, "request_id": str(uuid4())}
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await runtime.submit("catalog_care_write", {"request": stale})


async def test_group_paging_search_and_variant_filter_never_promote_a_child(runtime, config):
    catalog = await seed_catalog(runtime, config, CATALOGS[0])
    ids = list(all_rows(runtime.state, catalog))
    await runtime.submit("catalog_care_write", {"request": request(runtime, catalog, ids)})
    master = listing(runtime.state, catalog, grouped=True)["items"][0]
    found = listing(
        runtime.state, catalog, grouped=True, query="item 2", filters={"structure": "variants"}
    )
    assert [row["identity"] for row in found["items"]] == [master["identity"]]
    assert found["items"][0]["matches_filter"] is False
    assert found["items"][0]["matching_variant_count"] == 1
    children = detail(
        runtime.state,
        catalog,
        master["identity"],
        query="item 2",
        filters={"structure": "variants"},
    )
    assert [row["key"] for row in children["variants"]] == ["Synthetic item 2"]
    flat = listing(runtime.state, catalog)
    assert len(flat["items"]) == 3
    page = listing(runtime.state, catalog, grouped=True, limit=1)
    assert page["next_cursor"] is None
    # Cursors bind the representation as well as the content revision/filters.
    flat_page = listing(runtime.state, catalog, limit=1)
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        listing(runtime.state, catalog, grouped=True, limit=1, after=flat_page["next_cursor"])


def test_existing_care_request_digests_do_not_gain_an_optional_field():
    old = {
        "catalog_id": "synthetic",
        "definition_version": 1,
        "expected_active_revision": 1,
        "expected_content_revision": 2,
        "request_id": str(uuid4()),
        "reason": "Synthetic",
        "action": "group",
        "identities": ["one"],
        "master": "one",
        "classification": "normal",
    }
    assert CareRequest.model_validate(old).model_dump(mode="json") == old
    assert checksum(CareRequest.model_validate(old)) == checksum(old)
