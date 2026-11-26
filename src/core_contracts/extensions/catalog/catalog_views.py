"""Read-only cross-catalog pages and repeatable structure checks."""

import base64
import csv
import io
import json
import re
from collections import defaultdict
from typing import Any

from core_contracts.extensions.catalog.catalog_care import all_rows, candidate_key, epoch
from core_contracts.extensions.catalog.catalog_families import VERSION_PATTERN
from core_contracts.extensions.catalog.managed_catalog import head, head_key
from core_contracts.model import canonical, checksum
from core_contracts.rules.catalog_models import ManagedCatalog
from core_contracts.text_rules import normalize_key


def catalogs_for(config: Any, catalog_id: str, version: int) -> list[Any]:
    return (
        [
            item
            for item in config.catalogs
            if isinstance(item, ManagedCatalog)
            and (catalog_id == "all" or (item.catalog_id, item.version) == (catalog_id, version))
        ]
        if config
        else []
    )


def page_all(
    state: Any,
    config: Any,
    *,
    query: str = "",
    view: str = "all",
    after: str = "",
    filters: dict[str, str] | None = None,
    sort: str = "newest",
    variants_first: bool = False,
    limit: int = 100,
    classification_catalog: str = "",
    rows_by_catalog: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    from core_contracts.extensions.catalog.catalog_care import listing, validate_page

    filters = filters or {}
    validate_page(query, view, filters, sort, limit)
    if len(classification_catalog) > 256:
        raise ValueError("catalog_page_invalid")
    selected: list[dict[str, Any]] = []
    versions = []
    filter_values: dict[str, set[str]] = defaultdict(set)
    for catalog in catalogs_for(config, "all", 1):
        if (
            classification_catalog
            and classification_catalog != f"{catalog.catalog_id}@{catalog.version}"
        ):
            continue
        # Reuse the single-catalog validation and grouping, keeping pagination server-side.
        page = listing(
            state,
            catalog,
            query=query,
            view=view,
            filters=filters,
            sort=sort,
            variants_first=variants_first,
            grouped=True,
            _all=True,
            rows=rows_by_catalog.get(head_key(catalog)) if rows_by_catalog is not None else None,
        )
        selected.extend(
            {
                **row,
                "catalog_id": catalog.catalog_id,
                "definition_version": catalog.version,
                "content_revision": page["content_revision"],
            }
            for row in page["items"]
        )
        for key, values in page["filter_values"].items():
            filter_values[key].update(values)
        versions.append([catalog.catalog_id, catalog.version, head(state, catalog)["revision"]])

    def order(row: dict[str, Any]) -> tuple[Any, ...]:
        value: Any = (
            row["effective_classification"]
            if sort == "classification"
            else normalize_key(row.get("display_name") or row["key"])
            if sort == "title"
            else row["identity"]
            if sort == "identity"
            else (-1 if sort == "newest" else 1)
            * epoch(row.get("family_last_seen") or row.get("last_seen"))
        )
        return (
            0 if variants_first and row.get("candidate") else 1,
            value,
            row["catalog_id"],
            row["definition_version"],
            row["identity"],
        )

    selected.sort(key=order)
    binding = checksum(
        [
            state.active_revision,
            versions,
            query,
            view,
            filters,
            sort,
            variants_first,
            classification_catalog,
            [
                [
                    row["catalog_id"],
                    row["identity"],
                    row.get("family_seen_count"),
                    row.get("family_last_seen"),
                ]
                for row in selected
            ],
        ]
    )
    offset = 0
    if after:
        try:
            cursor_data = json.loads(base64.urlsafe_b64decode(after))
            offset = cursor_data["offset"]
            if cursor_data["binding"] != binding or type(offset) is not int or offset < 0:
                raise ValueError()
        except ValueError, KeyError, TypeError, UnicodeError:
            raise ValueError("catalog_content_conflict") from None
    if not 1 <= limit <= 100:
        raise ValueError("catalog_page_invalid")
    following = offset + limit
    return {
        "items": selected[offset:following],
        "content_revision": 0,
        "next_cursor": base64.urlsafe_b64encode(
            canonical({"binding": binding, "offset": following}).encode()
        ).decode()
        if following < len(selected)
        else None,
        "total": len(selected),
        "filter_values": {key: sorted(values) for key, values in filter_values.items()},
    }


def inspect_structure(
    state: Any, config: Any, catalog_id: str, version: int, after: int = 0
) -> dict[str, Any]:
    findings = []
    for catalog in catalogs_for(config, catalog_id, version):
        rows = all_rows(state, catalog)
        clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
        spellings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        artists: dict[str, set[str]] = defaultdict(set)
        for row in rows.values():
            seed = candidate_key(row)
            clusters[seed].append(row)
            spellings[
                canonical(
                    [row["scope"], row["media_type"], row["signal_type"], normalize_key(row["key"])]
                )
            ].append(row)
            if (
                not row.get("master_identity")
                and row.get("variant_count")
                and re.search(VERSION_PATTERN, row["key"], re.I)
            ):
                findings.append((catalog, "version_master", row))
            if parent := rows.get(row.get("master_identity") or ""):
                observed_parent = (
                    state.tables["catalog_sighting"]
                    .get(head_key(catalog) + ":" + row["identity"], {})
                    .get("master_key")
                )
                opaque_hierarchy = parent["key"].startswith(("studio:", "master:")) or row[
                    "key"
                ].startswith("item:")
                differs = (
                    normalize_key(observed_parent) != normalize_key(parent["key"])
                    if observed_parent
                    else not opaque_hierarchy and seed != candidate_key(parent)
                )
                if differs:
                    findings.append((catalog, "different_work", row))
            if row["media_type"] == "music":
                try:
                    pair = (
                        json.loads(row["key"])
                        if row["key"].startswith("[")
                        else row["key"].split(" - ", 1)
                    )
                except ValueError, RecursionError:
                    pair = []
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(isinstance(part, str) and part.strip() for part in pair)
                ):
                    findings.append((catalog, "missing_artist", row))
                else:
                    artist = normalize_key(pair[0])
                    artists[
                        canonical(
                            [
                                row["scope"],
                                row["media_type"],
                                row["signal_type"],
                                sorted(re.split(r"\s*(?:,|&|\band\b)\s*", artist)),
                            ]
                        )
                    ].add(artist)
        for members in clusters.values():
            roots = {row.get("master_identity") or row["identity"] for row in members}
            if len(roots) > 1 and len(members) > 1:
                findings.extend((catalog, "split_family", row) for row in members)
            findings.extend((catalog, "unassigned", row) for row in members if not row["complete"])
        for members in spellings.values():
            if len(members) > 1:
                findings.extend((catalog, "duplicate", row) for row in members)
        for variants in artists.values():
            if len(variants) > 1:
                findings.extend(
                    (catalog, "artist_spelling", row)
                    for row in rows.values()
                    if any(artist in row["key"].lower() for artist in variants)
                )
    items = [
        {
            "catalog_id": c.catalog_id,
            "definition_version": c.version,
            "kind": kind,
            "identity": row["identity"],
            "key": row["key"],
            "display_name": row.get("display_name", ""),
        }
        for c, kind, row in findings
    ]
    counts: dict[str, int] = defaultdict(int)
    for row in items:
        counts[row["kind"]] += 1
    return {
        "items": items[after : after + 100],
        "counts": dict(counts),
        "total": len(items),
        "next_offset": after + 100 if after + 100 < len(items) else None,
    }


def export_structure(state: Any, config: Any, catalog_id: str, version: int) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Katalog",
            "Version",
            "Kennung",
            "Titel",
            "Rolle",
            "Master",
            "Klasse",
            "Wiedergaben",
            "Letzte Wiedergabe",
        ]
    )

    def cell(value: Any) -> Any:
        # Spreadsheet programs must treat user titles as text, never formulas.
        return (
            "'" + value
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
            else value
        )

    for catalog in catalogs_for(config, catalog_id, version):
        for row in sorted(
            all_rows(state, catalog).values(),
            key=lambda item: (
                item.get("master_identity") or item["identity"],
                bool(item.get("master_identity")),
                item["identity"],
            ),
        ):
            writer.writerow(
                map(
                    cell,
                    [
                        catalog.catalog_id,
                        catalog.version,
                        row["identity"],
                        row.get("display_name") or row["key"],
                        "Variante"
                        if row.get("master_identity")
                        else "Master"
                        if row.get("variant_count")
                        else "Einzeln",
                        row.get("master_identity") or "",
                        row["effective_classification"],
                        row.get("family_seen_count", row.get("seen_count", 0)),
                        row.get("family_last_seen") or row.get("last_seen") or "",
                    ],
                )
            )
    return "\ufeff" + stream.getvalue()
