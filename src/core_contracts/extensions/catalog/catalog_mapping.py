"""Catalog mapping state, imported write bounds and canonical title keys."""

from collections.abc import Iterator
from typing import Any

from pydantic import Field

from core_contracts.model import canonical
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange

MAX_ENTRIES = 100_000


class ImportWrite(CatalogWrite):
    changes: tuple[EntryChange, ...] = Field(min_length=1, max_length=MAX_ENTRIES)


def current_mapping(state: Any) -> dict[str, Any]:
    records = state.tables.get("title_sync_mapping", {})
    return (
        max(records.values(), key=lambda row: row["version"])
        if records
        else {
            "version": 0,
            "mapping": {"mappings": {}, "standalone": []},
        }
    )


def music_forms(key: str) -> Iterator[str]:
    """Enumerate every runtime tuple; the first split is the primary form."""
    start = 0
    while (at := key.find(" - ", start)) >= 0:
        yield canonical([key[:at], key[at + 3 :]])
        start = at + 3
    yield canonical([key])


def keys(row: dict[str, Any], profile: str) -> list[str]:
    if profile != "music":
        return [row["key"]]
    if row.get("title") is not None and row.get("artist") is not None:
        return list(
            dict.fromkeys([canonical([row["artist"], row["title"]]), canonical([row["title"]])])
        )
    return list(music_forms(row["key"]))
