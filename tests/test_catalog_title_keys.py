import copy

import pytest

from core_contracts.catalog_care import all_rows, candidate_key, queue
from core_contracts.managed_catalog import head_key, identity
from core_contracts.model import canonical

from .test_catalog_care import seeded
from .test_title_sync import observed

ARTIST = "Synthetic Ensemble Example"


def entry(key, **updates):
    return {
        "key": key,
        "scope": "synthetic",
        "media_type": "music",
        "signal_type": "title",
        **updates,
    }


@pytest.mark.parametrize("with_artist", [False, True])
def test_runtime_versions_group_by_title_instead_of_artist(with_artist):
    def runtime_key(title):
        return canonical([ARTIST, title] if with_artist else [title])

    versions = ["Synthetic Beacon", "Synthetic Beacon (Remix)", "Synthetic Beacon (Edit)"]
    variants = {candidate_key(entry(runtime_key(title))) for title in versions}
    assert len(variants) == 1
    different = {
        candidate_key(entry(runtime_key(title)))
        for title in ["Separate Meadow", "Different Harbor"]
    }
    assert len(different) == 2
    assert variants.isdisjoint(different)


def test_runtime_and_separate_tc_fields_keep_the_same_existing_rules():
    source = entry(
        "Unrelated display key",
        tc_metadata={"artist": ARTIST, "title": "Synthetic Beacon (Live)"},
    )
    runtime = entry(canonical([ARTIST + " feat. Synthetic Guest", "Synthetic Beacon (Edit)"]))
    assert candidate_key(source) == candidate_key(runtime)
    conflicting_key = entry(
        canonical(["Synthetic Other", "Separate Meadow"]), tc_metadata=source["tc_metadata"]
    )
    assert candidate_key(conflicting_key) == candidate_key(source)


def test_single_element_title_with_delimiter_is_not_split_into_an_artist():
    title = "Synthetic - Beacon (Live)"
    assert candidate_key(entry(canonical([title]))) == candidate_key(
        entry("Synthetic metadata key", tc_metadata={"artist": "", "title": title})
    )


async def test_mixed_tc_and_local_sightings_propose_only_versions_without_mutation(runtime, config):
    source = observed("Synthetic Catalog Original", artist=ARTIST, title="Synthetic Beacon")
    catalog = await seeded(runtime, config, [source])
    for title in [
        "Synthetic Beacon (Remix)",
        "Synthetic Beacon (Edit)",
        "Separate Meadow",
        "Different Harbor",
    ]:
        row = entry(canonical([ARTIST, title]))
        ident = identity(row["scope"], row["media_type"], row["signal_type"], row["key"])
        runtime.state.tables["catalog_sighting"][head_key(catalog) + ":" + ident] = {
            **row,
            "catalog": head_key(catalog),
            "identity": ident,
            "first_seen": "2032-01-01T00:00:00Z",
            "last_seen": "2032-01-02T00:00:00Z",
            "seen_count": 2,
            "report": "synthetic",
        }
    before = copy.deepcopy(runtime.state.tables)
    result = queue(runtime.state, catalog, "", "all", {})
    assert len(result["groups"]) == 1
    members = result["groups"][0]["members"]
    assert len(members) == 3
    assert {row["key"] for row in members} == {
        canonical([ARTIST, "Synthetic Beacon"]),
        canonical([ARTIST, "Synthetic Beacon (Remix)"]),
        canonical([ARTIST, "Synthetic Beacon (Edit)"]),
    }
    rows = all_rows(runtime.state, catalog)
    assert sum(row["candidate"] for row in rows.values()) == 3
    assert runtime.state.tables == before
