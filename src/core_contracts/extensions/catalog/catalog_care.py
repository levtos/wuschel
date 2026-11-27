"""Catalog care views and bounded bulk requests; writes use managed_catalog.prepare."""

import asyncio
import base64
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field

from core_contracts.extensions.catalog.catalog_families import VERSION_PATTERN
from core_contracts.extensions.catalog.managed_catalog import (
    class_view,
    discovered_entries,
    effective,
    head,
    head_key,
    identity,
    semantic,
)
from core_contracts.model import Model, canonical, checksum
from core_contracts.rules.catalog_models import (
    CatalogWrite,
    ClassKey,
    ClassValue,
    EntryChange,
    ManagedCatalog,
)
from core_contracts.text_rules import normalize_key


class CareRequest(Model):
    catalog_id: str
    definition_version: int = Field(ge=1)
    expected_active_revision: int = Field(ge=0, strict=True)
    expected_content_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    reason: str = Field(min_length=1, max_length=256)
    action: Literal[
        "classify",
        "group",
        "ungroup",
        "promote",
        "review",
        "open",
        "hide",
        "unhide",
        "standalone_review",
    ]
    identities: tuple[str, ...] = Field(min_length=1, max_length=100)
    master: str | None = None
    new_master_name: str | None = Field(
        default=None, min_length=1, max_length=1024, exclude_if=lambda value: value is None
    )
    classification: str | None = None
    class_key: ClassKey | None = Field(default=None, exclude_if=lambda value: value is None)
    class_value: ClassValue | None = Field(default=None, exclude_if=lambda value: value is None)
    class_values: dict[ClassKey, ClassValue] | None = Field(
        default=None, max_length=64, exclude_if=lambda value: value is None
    )
    confirm_class_change: bool = Field(default=False, exclude_if=lambda value: not value)


class CareWrite(CatalogWrite):
    changes: tuple[EntryChange, ...] = Field(max_length=1000)


def fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key in EntryChange.model_fields}


def status(row: dict[str, Any], entries: dict[str, Any]) -> str:
    if row.get("hidden_at"):
        return "hidden"
    master = entries.get(row.get("master_identity") or "", {})
    if row.get("reviewed_at") or master.get("reviewed_at") or master.get("hidden_at"):
        return "reviewed"
    return "open"


def candidate_key(row: dict[str, Any]) -> str:
    metadata = row.get("tc_metadata") or {}
    text = row["key"]
    parts = text.split(" - ", 1)
    # Runtime compound keys carry artist/title separately, just like TC metadata.
    try:
        decoded = json.loads(row["key"]) if row["key"].startswith("[") else None
    except ValueError, RecursionError:
        decoded = None
    if (
        isinstance(decoded, list)
        and len(decoded) in {1, 2}
        and all(isinstance(value, str) for value in decoded)
    ):
        parts = decoded if len(decoded) == 2 else ["", decoded[0]]
    artist = (
        metadata.get("artist")
        if metadata.get("artist") is not None
        else parts[0]
        if len(parts) > 1
        else ""
    )
    title = metadata.get("title") if metadata.get("title") is not None else parts[-1]
    artist = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", str(artist or ""), flags=re.I)[0]

    seed = [
        normalize_key(artist),
        normalize_key(re.sub(VERSION_PATTERN, " ", str(title or ""), flags=re.I)),
    ]
    return (
        canonical([row["scope"], row["media_type"], row["signal_type"], *seed]) if any(seed) else ""
    )


def master_order(row: dict[str, Any]) -> tuple[Any, ...]:
    text = canonical([row.get("tc_metadata") or {}, row["key"]])
    return (
        -bool(row.get("variant_count")),
        bool(re.search(VERSION_PATTERN, text, re.I)),
        -bool(re.search(r"\b(?:feat\.?|ft\.?|featuring)\s", text, re.I)),
        -row.get("seen_count", 0),
        epoch(row.get("first_seen")),
        row["identity"],
    )


def all_rows(
    state: Any, catalog: Any, *, identities: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    entries = discovered_entries(state, catalog)
    rows = {
        key: {"identity": key, **row}
        for key, row in entries.items()
        if (identities is None or key in identities)
        and not row["tombstone"]
        and not (row.get("tc_metadata") or {}).get("is_alias")
    }
    owners = {
        canonical(
            [row["scope"], row["media_type"], row["signal_type"], metadata["source_identity"]]
        ): key
        for key, row in rows.items()
        if (metadata := row.get("tc_metadata"))
    }
    for sighting in state.tables["catalog_sighting"].values():
        if sighting["catalog"] != head_key(catalog):
            continue
        key = sighting["identity"]
        if entries.get(key, {}).get("tombstone") and not sighting.get("after_removal"):
            continue
        metadata = entries.get(key, {}).get("tc_metadata") or {}
        if metadata.get("is_alias"):
            key = owners.get(
                canonical(
                    [
                        sighting["scope"],
                        sighting["media_type"],
                        sighting["signal_type"],
                        metadata["source_identity"],
                    ]
                )
            )
            if key is None:
                continue
        if identities is not None and key not in identities:
            continue
        row = rows.setdefault(
            key,
            {
                **sighting,
                "classification": None,
                "master_key": None,
                "display_name": "",
                "note": "",
            },
        )
        local = {field: sighting[field] for field in ("first_seen", "last_seen", "seen_count")}
        if previous := row.get("local_sightings"):
            local = {
                "first_seen": min(previous["first_seen"], local["first_seen"], key=epoch),
                "last_seen": max(previous["last_seen"], local["last_seen"], key=epoch),
                "seen_count": previous["seen_count"] + local["seen_count"],
            }
        row["local_sightings"] = local
        if not row.get("display_name"):
            row["display_name"] = sighting.get("display_name", "")
        if not row.get("tc_metadata"):
            row.update(row["local_sightings"])
    children: dict[str, int] = defaultdict(int)
    for row in entries.values():
        if (
            row.get("master_identity")
            and not row["tombstone"]
            and not (row.get("tc_metadata") or {}).get("is_alias")
        ):
            children[row["master_identity"]] += 1
    clusters: dict[str, list[str]] = defaultdict(list)
    for key, row in rows.items():
        value, complete, master = effective(entries, key, catalog)
        dimensions = class_view(entries, key, catalog)
        row.update(dimensions)
        if dimensions:
            complete = all(origin != "default" for origin in dimensions["class_origins"].values())
        row.update(
            effective_classification=value,
            complete=complete,
            master_identity=master,
            review_status=status(row, entries),
            variant_count=children.get(key, 0),
            semantic_digest=semantic(entries, key, catalog),
        )
        telemetry = (row.get("tc_metadata") or {}).get("telemetry")
        if not row.get("display_name"):
            row["display_name"] = (row.get("tc_metadata") or {}).get("app_name") or ""
        if row.get("signal_type") == "app":
            row["display_name"] = state.tables["service_state"].get(
                "catalog_app_name:" + checksum(row["key"]), {}
            ).get("display_name") or row.get("display_name", "")
        if telemetry:
            row.update(telemetry)
        # Wuschel playback dates take precedence over historical import dates;
        # keep the original telemetry unchanged in tc_metadata.
        if local_sightings := row.get("local_sightings"):
            row.update({field: local_sightings[field] for field in ("first_seen", "last_seen")})
        if state.tables["service_state"].get("catalog_counter_epoch"):
            row["seen_count"] = row.get("local_sightings", {}).get("seen_count", 0)
        row["playback_counts"] = bool(state.tables["service_state"].get("catalog_counter_epoch"))
        if (
            sighting_metadata := state.tables["catalog_sighting"]
            .get(head_key(catalog) + ":" + key, {})
            .get("display_metadata")
        ):
            row["display_metadata"] = sighting_metadata
        if (
            media_metadata := state.tables["catalog_sighting"]
            .get(head_key(catalog) + ":" + key, {})
            .get("catalog_metadata")
        ):
            row["catalog_metadata"] = media_metadata
        if metadata := row.get("catalog_metadata"):
            name = (
                state.tables["service_state"]
                .get("catalog_app_name:" + checksum(metadata["app_id"]), {})
                .get("display_name")
            )
            if name:
                row["catalog_metadata"] = {**metadata, "app_name": name}
        seed = candidate_key(row) if not master and row["review_status"] != "hidden" else ""
        row["cluster_key"] = seed
        if seed:
            clusters[seed].append(key)
    for row in rows.values():
        row["candidate"] = len(clusters.get(row["cluster_key"], [])) > 1
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows.values():
        families[row.get("master_identity") or row["identity"]].append(row)
    for root, members in families.items():
        if root in rows:
            rows[root]["family_seen_count"] = sum(row.get("seen_count", 0) for row in members)
            rows[root]["family_last_seen"] = max(
                (row.get("last_seen") for row in members if row.get("last_seen")),
                default=None,
                key=epoch,
            )
    for index, row in enumerate(sorted(rows.values(), key=master_order)):
        row["master_order"] = index
    return rows


class CatalogReadCache:
    """Derived HTTP rows for the current published catalog content and sightings."""

    def __init__(self) -> None:
        self._revision: tuple[str, int] | None = None
        self._sightings: dict[str, dict[str, Any]] = {}
        self._by_catalog: dict[str, dict[str, dict[str, Any]]] = {}
        self._rows: dict[str, tuple[Any, dict[str, dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def rows(self, state: Any, catalog: ManagedCatalog) -> dict[str, dict[str, Any]]:
        async with self._lock:
            revision = (state.epoch_id, state.active_revision)
            if revision != self._revision:
                self._rows.clear()
                self._revision = revision
            sightings = state.tables["catalog_sighting"]
            if sightings != self._sightings:
                self._sightings = dict(dict.items(sightings))
                by_catalog: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
                for key, row in self._sightings.items():
                    by_catalog[row["catalog"]][key] = row
                self._by_catalog = by_catalog
            key = head_key(catalog)
            heads = state.tables["catalog_head"]
            current = dict.get(heads, key)
            local = self._by_catalog.get(key, {})
            metadata = {
                name: row
                for name, row in dict.items(state.tables["service_state"])
                if name == "catalog_counter_epoch" or name.startswith("catalog_app_name:")
            }
            basis = (current, local, metadata)
            cached = self._rows.get(key)
            if cached is not None and cached[0] == basis:
                return cached[1]

            # Published rows are immutable. Copy the selected tables inside the worker
            # before the view reads them, keeping row leases on the writer's loop.
            tables = {
                "catalog_head": {key: current} if current is not None else {},
                "catalog_sighting": local,
                "service_state": metadata,
            }

            def build() -> dict[str, dict[str, Any]]:
                return all_rows(SimpleNamespace(tables=deepcopy(tables)), catalog)

            rows = await asyncio.to_thread(build)
            self._rows[key] = (basis, rows)
            return rows


def filtered(
    rows: dict[str, dict[str, Any]], query: str, view: str, filters: dict[str, str]
) -> list[dict[str, Any]]:
    result = []
    for row in rows.values():
        if query and normalize_key(query) not in normalize_key(
            row.get("display_name") or row["key"]
        ):
            continue
        if view in {"open", "reviewed", "hidden"} and row["review_status"] != view:
            continue
        if (
            view == "unassigned"
            and row["complete"]
            or view == "masters"
            and not (row.get("master_identity") or row.get("variant_count"))
        ):
            continue
        if any(
            value and row.get(key) != value
            for key, value in filters.items()
            if key in {"scope", "media_type", "signal_type"}
        ):
            continue
        metadata = row.get("catalog_metadata") or {}
        if any(
            value
            and value
            != (
                str(bool(metadata.get("live_tv"))).lower()
                if key == "live_tv"
                else str(metadata.get("app_id" if key == "app" else key, ""))
            )
            for key, value in filters.items()
            if key in {"app", "live_tv", "sender"}
        ):
            continue
        if filters.get("context") and not any(
            filters["context"]
            in {ctx["context"], canonical([ctx["context"], ctx.get("source_app", "")])}
            for ctx in (row.get("tc_metadata") or {}).get("contexts", [])
        ):
            continue
        structure = filters.get("structure")
        if (
            structure == "variants"
            and not row.get("master_identity")
            or structure == "masters"
            and not row["variant_count"]
        ):
            continue
        if filters.get("classification") == "__unassigned__" and row["complete"]:
            continue
        if filters.get("classification") not in {
            None,
            "",
            "__unassigned__",
            row["effective_classification"],
        }:
            continue
        key = filters.get("class_key")
        expected = filters.get("class_value")
        if (
            key
            and expected
            and (
                key not in row.get("effective_class_values", {})
                or canonical(row["effective_class_values"][key]) != expected
            )
        ):
            continue
        result.append(row)
    return result


def epoch(value: Any) -> float:
    return datetime.fromisoformat(value).timestamp() if value else 0


def validate_page(query: str, view: str, filters: dict[str, str], sort: str, limit: int) -> None:
    if (
        (
            sort not in {"identity", "newest", "oldest", "title", "classification"}
            and not re.fullmatch(r"class:[a-z][a-z0-9_]{0,63}", sort)
        )
        or view not in {"all", "unassigned", "masters", "open", "reviewed", "hidden"}
        or filters.keys()
        - {
            "scope",
            "media_type",
            "signal_type",
            "context",
            "structure",
            "app",
            "live_tv",
            "sender",
            "classification",
            "class_key",
            "class_value",
        }
        or any(len(value) > 16384 for value in filters.values())
        or len(query) > 1024
        or not 1 <= limit <= 100
    ):
        raise ValueError("catalog_page_invalid")


def listing(
    state: Any,
    catalog: Any,
    *,
    query: str = "",
    view: str = "all",
    after: str = "",
    limit: int = 100,
    filters: dict[str, str] | None = None,
    sort: str = "identity",
    variants_first: bool = False,
    grouped: bool = False,
    _all: bool = False,
    rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    filters = filters or {}
    validate_page(query, view, filters, sort, limit)
    current = head(state, catalog)
    if rows is None:
        rows = all_rows(state, catalog)
    media_metadata = [
        row["catalog_metadata"] for row in rows.values() if row.get("catalog_metadata")
    ]
    selected = filtered(rows, query, view, filters)

    def order(row: dict[str, Any]) -> tuple[Any, ...]:
        if sort.startswith("class:"):
            value = row.get("effective_class_values", {}).get(sort[6:])
            return (
                0 if variants_first and row["candidate"] else 1,
                value is None,
                value,
                row["identity"],
            )
        key = (
            row["effective_classification"]
            if sort == "classification"
            else row["identity"]
            if sort == "identity"
            else normalize_key(row.get("display_name") or row["key"])
            if sort == "title"
            else (
                (-1 if sort == "newest" else 1) * epoch(row.get("last_seen")),
                (-1 if sort == "newest" else 1) * epoch(row.get("first_seen")),
            )
        )
        return (0 if variants_first and row["candidate"] else 1, key, row["identity"])

    matching_entries = len(selected)
    if grouped:
        groups: dict[str, dict[str, Any]] = {}
        ranks: dict[str, tuple[Any, ...]] = {}
        matched = {row["identity"] for row in selected}
        for row in selected:
            root = row.get("master_identity") or row["identity"]
            if root not in groups:
                groups[root] = {
                    **rows[root],
                    "matches_filter": root in matched,
                    "matching_variant_count": 0,
                }
                ranks[root] = order(row)
            ranks[root] = min(ranks[root], order(row))
            if row["identity"] != root:
                groups[root]["matching_variant_count"] += 1
        selected = sorted(groups.values(), key=lambda row: ranks[row["identity"]])
    else:
        selected.sort(key=order)
    binding = checksum(
        [
            head_key(catalog),
            current["revision"],
            query,
            view,
            filters,
            sort,
            variants_first,
            grouped,
        ]
    )
    offset = 0
    if after:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(after))
            if (
                cursor["binding"] != binding
                or type(cursor["offset"]) is not int
                or cursor["offset"] < 0
            ):
                raise ValueError()
            offset = cursor["offset"]
        except ValueError, KeyError, TypeError, UnicodeError:
            raise ValueError("catalog_content_conflict") from None
    page = selected if _all else selected[offset : offset + limit]
    from core_contracts.extensions.catalog.catalog_mapping import current_mapping

    next_cursor = (
        base64.urlsafe_b64encode(
            canonical({"binding": binding, "offset": offset + limit}).encode()
        ).decode()
        if not _all and offset + limit < len(selected)
        else None
    )
    return {
        "items": page,
        "content_revision": current["revision"],
        "next_cursor": next_cursor,
        "total": len(selected),
        **({"matching_entries": matching_entries} if grouped else {}),
        "filter_values": {
            **{
                field: sorted({r[field] for r in rows.values()})
                for field in ("scope", "media_type", "signal_type")
            },
            **(
                {
                    field: sorted(
                        {
                            str(metadata.get("app_id" if field == "app" else field, ""))
                            for metadata in media_metadata
                        }
                        - {""}
                    )
                    for field in ("app", "sender")
                }
                if media_metadata
                else {}
            ),
        },
        **(
            {
                "app_labels": {
                    metadata["app_id"]: metadata.get("app_name") or ""
                    for metadata in media_metadata
                }
            }
            if media_metadata
            else {}
        ),
        "context_targets": {
            key: {"catalog_id": target["catalog_id"], "scope": target["scope"]}
            for key, target in current_mapping(state)["mapping"]["mappings"].items()
        },
        "contexts": sorted(
            {
                canonical([ctx["context"], ctx.get("source_app", "")])
                for row in rows.values()
                for ctx in (row.get("tc_metadata") or {}).get("contexts", [])
            }
        ),
    }


def queue(
    state: Any,
    catalog: Any,
    query: str,
    view: str,
    filters: dict[str, str],
    *,
    rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected = filtered(all_rows(state, catalog) if rows is None else rows, query, view, filters)
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        if row["candidate"]:
            clusters[row["cluster_key"]].append(row)
    result = []
    for members in sorted(clusters.values(), key=lambda values: -len(values)):
        if len(members) < 2:
            continue
        master = min(members, key=master_order)
        result.append(
            {
                "members": [
                    {
                        key: row.get(key)
                        for key in (
                            "identity",
                            "key",
                            "display_name",
                            "classification",
                            "scope",
                            "media_type",
                            "signal_type",
                            "master_identity",
                            "master_key",
                            "variant_count",
                            "effective_classification",
                            "complete",
                        )
                    }
                    for row in members
                ],
                "master": master["identity"],
            }
        )
    return {
        "groups": result,
        "content_revision": head(state, catalog)["revision"],
        "active_revision": state.active_revision,
    }


def change_request(state: Any, catalog: Any, request: CareRequest, now: Any) -> CareWrite:
    current = head(state, catalog)
    if state.active_revision != request.expected_active_revision:
        raise ValueError("active_revision_conflict")
    if current["revision"] != request.expected_content_revision:
        raise ValueError("catalog_content_conflict")
    rows = all_rows(state, catalog, identities=set(request.identities))
    if len(set(request.identities)) != len(request.identities) or any(
        key not in rows for key in request.identities
    ):
        raise ValueError("catalog_page_invalid")
    selected = {key: fields(rows[key]) for key in request.identities}
    action = request.action
    named = bool(catalog.class_assignments)
    if request.class_key and request.class_key not in catalog.assigned_classes:
        raise ValueError("catalog_class_invalid")
    if request.class_value is not None and not request.class_key:
        raise ValueError("catalog_class_invalid")
    if request.class_values is not None:
        catalog.validate_classes(request.class_values)
    if action == "promote":
        if len(request.identities) != 1:
            raise ValueError("catalog_page_invalid")
        child = rows[request.identities[0]]
        old_root = child.get("master_identity")
        if not old_root:
            raise ValueError("catalog_master_missing")
        family = all_rows(state, catalog)
        selected = {
            key: fields(row)
            for key, row in family.items()
            if key == old_root or row.get("master_identity") == old_root
        }
        if len(selected) > 1000:
            raise ValueError("catalog_page_invalid")
        value = (
            request.classification
            if request.classification is not None
            else family[old_root]["effective_classification"]
        )
        dimensions = (
            request.class_values
            if request.class_values is not None
            else family[old_root].get("class_values", {})
        )
        changes_class = (
            dimensions != family[old_root].get("class_values", {})
            if named
            else value != family[old_root]["effective_classification"]
        )
        if changes_class and not request.confirm_class_change:
            raise ValueError("catalog_confirm_class_change")
        for key, row in selected.items():
            row.update(
                master_key=None if key == child["identity"] else child["key"],
                classification=value if key == child["identity"] and not named else None,
                reviewed_at=now.isoformat(),
            )
            if named:
                row["class_values"] = dimensions if key == child["identity"] else {}
    target_master = request.master
    if request.new_master_name is not None and action != "group":
        raise ValueError("catalog_master_name_invalid")
    if action == "group":
        if request.new_master_name is not None:
            if request.master is not None or not normalize_key(request.new_master_name):
                raise ValueError("catalog_master_name_invalid")
            name = request.new_master_name.strip()
            first = rows[request.identities[0]]
            target_master = identity(
                first["scope"], first["media_type"], first["signal_type"], name
            )
            if (
                target_master in current["entries"]
                or target_master in discovered_entries(state, catalog)
                or head_key(catalog) + ":" + target_master in state.tables["catalog_sighting"]
            ):
                raise ValueError("catalog_master_name_conflict")
            if request.classification is None and not named:
                raise ValueError("catalog_class_invalid")
            master_row = {
                **{field: first[field] for field in ("scope", "media_type", "signal_type")},
                "key": name,
                "display_name": name,
                "classification": request.classification,
                "master_key": None,
                "note": "",
                "tombstone": False,
            }
        else:
            if target_master is None:
                raise ValueError("catalog_master_missing")
            if target_master not in selected:
                raise ValueError("catalog_master_not_selected")
            master_row = rows[target_master]
        for key in selected:
            if any(
                rows[key][field] != master_row[field]
                for field in ("scope", "media_type", "signal_type")
            ):
                raise ValueError("catalog_master_scope_mismatch")
        value = request.classification
        if value is None and target_master in rows:
            master = rows[target_master]
            if master["complete"]:
                value = master["effective_classification"]
        roots = {key for key in selected if key != target_master and rows[key]["variant_count"]}
        if previous_master := master_row.get("master_identity"):
            roots.add(previous_master)
        if roots:
            family = all_rows(state, catalog)
            rows.update(
                {
                    key: row
                    for key, row in family.items()
                    if key in roots or row.get("master_identity") in roots
                }
            )
            selected.update({key: fields(rows[key]) for key in rows})
        effective_class = value if value is not None else catalog.unassigned_value
        dimensions = request.class_values
        if dimensions is None:
            dimensions = {
                key: val
                for key, val in master_row.get("effective_class_values", {}).items()
                if master_row.get("class_origins", {}).get(key) != "default"
            }
        if (
            named
            and not request.confirm_class_change
            and any(
                key != target_master
                and any(
                    val != ({**catalog.class_defaults(), **dimensions}).get(name)
                    for name, val in rows[key].get("effective_class_values", {}).items()
                )
                for key in selected
            )
        ):
            raise ValueError("catalog_confirm_class_change")
        if (
            not named
            and not request.confirm_class_change
            and any(
                (
                    key in roots
                    or rows[key].get("master_identity") not in (None, target_master)
                    or request.classification is None
                    and key != target_master
                )
                and rows[key]["complete"]
                and rows[key]["effective_classification"] != effective_class
                for key in selected
            )
        ):
            raise ValueError("catalog_confirm_class_change")
        # Re-selecting an existing family is a no-op, including its review timestamps.
        selected = {
            key: row
            for key, row in selected.items()
            if (
                row.get("master_key") is not None
                or (
                    row.get("class_values", {}) != dimensions
                    if named
                    else row.get("classification") != value
                )
                if key == target_master
                else rows[key].get("master_identity") != target_master
            )
        }
        if selected and target_master in rows and target_master not in current["entries"]:
            selected[target_master] = fields(rows[target_master])
        if request.new_master_name is not None:
            assert target_master is not None
            selected[target_master] = master_row
    for key, row in selected.items():
        if action in {"classify", "ungroup"}:
            if action == "classify" and rows[key].get("master_identity"):
                raise ValueError("catalog_variant_override")
            if named:
                updated = dict(row.get("class_values", {}))
                if action == "ungroup":
                    updated = {
                        name: val
                        for name, val in rows[key].get("effective_class_values", {}).items()
                        if rows[key].get("class_origins", {}).get(name) == "inherited"
                    }
                if request.class_key:
                    if request.class_value is None:
                        updated.pop(request.class_key, None)
                    else:
                        updated[request.class_key] = request.class_value
                elif action == "classify":
                    raise ValueError("catalog_class_invalid")
                row.update(classification=None, class_values=updated, master_key=None)
            else:
                if request.classification is None:
                    raise ValueError("catalog_variant_override")
                row.update(classification=request.classification, master_key=None)
        if action == "group":
            if key == target_master:
                row.update(classification=None if named else value, master_key=None)
            else:
                row.update(classification=None, master_key=master_row["key"])
            if named:
                row["class_values"] = dimensions if key == target_master else {}
        if action in {"review", "standalone_review", "group", "classify", "ungroup"}:
            row["reviewed_at"] = now.isoformat()
        if action == "open":
            row["reviewed_at"] = None
        if action == "hide":
            row["hidden_at"] = now.isoformat()
        if action == "unhide":
            row["hidden_at"] = None
    # Ownership is provenance, not the inherited master: an existing variant's
    # aliases already point directly at its master and must move when detached.
    owners = {
        canonical(
            [
                row["scope"],
                row["media_type"],
                row["signal_type"],
                row["tc_metadata"]["source_identity"],
            ]
        ): key
        for key, row in selected.items()
        if row.get("tc_metadata")
    }
    for key, row in current["entries"].items():
        metadata = row.get("tc_metadata") or {}
        if not metadata.get("is_alias"):
            continue
        owner = owners.get(
            canonical(
                [
                    row["scope"],
                    row["media_type"],
                    row["signal_type"],
                    metadata.get("source_identity"),
                ]
            )
        )
        if metadata.get("is_alias") and owner is not None:
            primary = selected[owner]
            selected[key] = {
                **fields(row),
                "master_key": primary.get("master_key") or primary["key"],
            }
    return CareWrite.model_validate(
        {
            **{
                key: value
                for key, value in request.model_dump().items()
                if key in CatalogWrite.model_fields
            },
            "changes": list(selected.values()),
        }
    )


def detail(
    state: Any,
    catalog: Any,
    key: str,
    after: str = "",
    *,
    query: str = "",
    view: str = "all",
    filters: dict[str, str] | None = None,
    rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from core_contracts.extensions.catalog.catalog_mapping import current_mapping, keys
    from core_contracts.extensions.catalog.managed_catalog import definition, identity
    from core_contracts.registry import RegistryConfig

    filters = filters or {}
    if (
        len(query) > 1024
        or view not in {"all", "unassigned", "masters", "open", "reviewed", "hidden"}
        or filters.keys()
        - {
            "scope",
            "media_type",
            "signal_type",
            "context",
            "structure",
            "app",
            "live_tv",
            "sender",
            "classification",
            "class_key",
            "class_value",
        }
        or any(len(value) > 16384 for value in filters.values())
    ):
        raise ValueError("catalog_page_invalid")
    if rows is None:
        rows = all_rows(state, catalog)
    row = rows.get(key)
    if row is None:
        raise ValueError("catalog_page_invalid")
    child_rows = {
        ident: item
        for ident, item in rows.items()
        if item.get("master_identity") == key and ident > after
    }
    # Master/variant filters choose the outer groups, not the children displayed
    # within a group. Search, device, context and review filters still apply.
    variants = sorted(
        filtered(
            child_rows,
            query,
            "all" if view == "masters" else view,
            {name: value for name, value in filters.items() if name != "structure"},
        ),
        key=lambda item: item["identity"],
    )
    contexts = []
    source = row.get("tc_metadata") or {}
    config = RegistryConfig.model_validate(
        state.tables["registry_revision"][str(state.active_revision)]["config"]
    )
    mapping = current_mapping(state)["mapping"]["mappings"]
    for context in source.get("contexts", []):
        target = mapping.get(canonical([context["context"], context.get("source_app", "")]))
        resolved = None
        if target:
            try:
                device_catalog = definition(
                    config, target["catalog_id"], target["definition_version"]
                )
                source_row = {
                    "key": row.get("display_name") or row["key"],
                    **{name: source.get(name) for name in ("artist", "title")},
                }
                device_key = identity(
                    target["scope"],
                    row["media_type"],
                    row["signal_type"],
                    keys(source_row, target["profile"])[0],
                )
                value, complete, _ = effective(
                    head(state, device_catalog)["entries"], device_key, device_catalog
                )
                if complete:
                    resolved = value
            except ValueError:
                pass
        contexts.append(
            {
                **context,
                "catalog_id": target["catalog_id"] if target else None,
                "effective_classification": resolved,
            }
        )
    return {
        "entry": row,
        "contexts": contexts,
        "master": rows.get(row.get("master_identity") or ""),
        "variants": variants[:100],
        "next_cursor": variants[99]["identity"] if len(variants) > 100 else None,
        "content_revision": head(state, catalog)["revision"],
        "active_revision": state.active_revision,
    }
