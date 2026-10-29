"""Catalog care views and bounded bulk requests; writes use managed_catalog.prepare."""

import base64
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .catalog_models import CatalogWrite, EntryChange
from .managed_catalog import effective, head, head_key, semantic
from .model import Model, canonical, checksum
from .text_rules import normalize_key


class CareRequest(Model):
    catalog_id: str
    definition_version: int = Field(ge=1)
    expected_active_revision: int = Field(ge=0, strict=True)
    expected_content_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    reason: str = Field(min_length=1, max_length=256)
    action: Literal[
        "classify", "group", "ungroup", "review", "open", "hide", "unhide", "standalone_review"
    ]
    identities: tuple[str, ...] = Field(min_length=1, max_length=100)
    master: str | None = None
    classification: str | None = None


class CareWrite(CatalogWrite):
    changes: tuple[EntryChange, ...] = Field(min_length=1, max_length=1000)


def fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key in EntryChange.model_fields}


def status(row: dict[str, Any], entries: dict[str, Any]) -> str:
    if row.get("hidden_at"):
        return "hidden"
    master = entries.get(row.get("master_identity") or "", {})
    if row.get("reviewed_at") or master.get("reviewed_at") or master.get("hidden_at"):
        return "reviewed"
    if not row.get("tc_metadata") and row.get("classification") is not None:
        return "reviewed"
    return "open"


def candidate_key(row: dict[str, Any]) -> str:
    metadata = row.get("tc_metadata") or {}
    text = row.get("display_name") or row["key"]
    parts = text.split(" - ", 1)
    artist = (
        metadata.get("artist")
        if metadata.get("artist") is not None
        else parts[0]
        if len(parts) > 1
        else ""
    )
    title = metadata.get("title") if metadata.get("title") is not None else parts[-1]
    artist = re.split(
        r"\s+(?:feat\.?|ft\.?|featuring)\s+|\s*[&,]\s*", str(artist or ""), flags=re.I
    )[0]

    def words(value: str) -> list[str]:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).split()

    seed = [
        " ".join(words(artist)[:2]),
        " ".join(words(re.sub(r"\([^)]*\)", " ", str(title or "")))[:3]),
    ]
    return (
        canonical([row["scope"], row["media_type"], row["signal_type"], *seed]) if any(seed) else ""
    )


def all_rows(state: Any, catalog: Any) -> dict[str, dict[str, Any]]:
    current = head(state, catalog)
    entries = current["entries"]
    rows = {
        key: {"identity": key, **row}
        for key, row in entries.items()
        if not row["tombstone"] and not (row.get("tc_metadata") or {}).get("is_alias")
    }
    for sighting in state.tables["catalog_sighting"].values():
        if sighting["catalog"] != head_key(catalog):
            continue
        key = sighting["identity"]
        if (entries.get(key, {}).get("tc_metadata") or {}).get("is_alias"):
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
        row["local_sightings"] = {
            field: sighting[field] for field in ("first_seen", "last_seen", "seen_count")
        }
        if not row.get("tc_metadata"):
            row.update(row["local_sightings"])
    children: dict[str, int] = defaultdict(int)
    for row in rows.values():
        if row.get("master_identity"):
            children[row["master_identity"]] += 1
    clusters: dict[str, list[str]] = defaultdict(list)
    for key, row in rows.items():
        value, complete, master = effective(entries, key, catalog)
        row.update(
            effective_classification=value,
            complete=complete,
            master_identity=master,
            review_status=status(row, entries),
            variant_count=children.get(key, 0),
            semantic_digest=semantic(entries, key, catalog),
        )
        telemetry = (row.get("tc_metadata") or {}).get("telemetry")
        if telemetry:
            row.update(telemetry)
        seed = candidate_key(row) if not master and row["review_status"] != "hidden" else ""
        row["cluster_key"] = seed
        if seed:
            clusters[seed].append(key)
    for row in rows.values():
        row["candidate"] = len(clusters.get(row["cluster_key"], [])) > 1
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
            and row.get("master_identity")
        ):
            continue
        if any(
            value and row.get(key) != value
            for key, value in filters.items()
            if key in {"scope", "media_type", "signal_type"}
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
        result.append(row)
    return result


def epoch(value: Any) -> float:
    return datetime.fromisoformat(value).timestamp() if value else 0


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
) -> dict[str, Any]:
    filters = filters or {}
    if (
        sort not in {"identity", "newest", "oldest", "title"}
        or view not in {"all", "unassigned", "masters", "open", "reviewed", "hidden"}
        or filters.keys() - {"scope", "media_type", "signal_type", "context", "structure"}
        or any(len(value) > 16384 for value in filters.values())
        or len(query) > 1024
        or not 1 <= limit <= 100
    ):
        raise ValueError("catalog_page_invalid")
    current = head(state, catalog)
    selected = filtered(all_rows(state, catalog), query, view, filters)

    def order(row: dict[str, Any]) -> tuple[Any, ...]:
        key = (
            row["identity"]
            if sort == "identity"
            else normalize_key(row.get("display_name") or row["key"])
            if sort == "title"
            else (
                (-1 if sort == "newest" else 1) * epoch(row.get("last_seen")),
                (-1 if sort == "newest" else 1) * epoch(row.get("first_seen")),
            )
        )
        return (0 if variants_first and row["candidate"] else 1, key, row["identity"])

    selected.sort(key=order)
    binding = checksum(
        [head_key(catalog), current["revision"], query, view, filters, sort, variants_first]
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
    page = selected[offset : offset + limit]
    next_cursor = (
        base64.urlsafe_b64encode(
            canonical({"binding": binding, "offset": offset + limit}).encode()
        ).decode()
        if offset + limit < len(selected)
        else None
    )
    return {
        "items": page,
        "content_revision": current["revision"],
        "next_cursor": next_cursor,
        "total": len(selected),
        "contexts": sorted(
            {
                canonical([ctx["context"], ctx.get("source_app", "")])
                for row in all_rows(state, catalog).values()
                for ctx in (row.get("tc_metadata") or {}).get("contexts", [])
            }
        ),
    }


def queue(
    state: Any, catalog: Any, query: str, view: str, filters: dict[str, str]
) -> dict[str, Any]:
    rows = filtered(all_rows(state, catalog), query, view, filters)
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["candidate"]:
            clusters[row["cluster_key"]].append(row)
    result = []
    for members in sorted(clusters.values(), key=lambda values: -len(values)):
        if len(members) < 2:
            continue
        master = min(
            members,
            key=lambda r: (
                -bool(r["variant_count"]),
                -r.get("seen_count", 0),
                epoch(r.get("first_seen")),
                len(r.get("normalized_key", r["key"])),
                r["identity"],
            ),
        )
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
    rows = all_rows(state, catalog)
    if len(set(request.identities)) != len(request.identities) or any(
        key not in rows for key in request.identities
    ):
        raise ValueError("catalog_page_invalid")
    selected = {key: fields(rows[key]) for key in request.identities}
    action = request.action
    if action == "group":
        if request.master is None:
            raise ValueError("catalog_master_invalid")
        if request.master not in selected:
            raise ValueError("catalog_master_invalid")
        master_row = rows[request.master]
        if master_row.get("master_identity"):
            raise ValueError("catalog_master_invalid")
        for key in selected:
            if any(
                rows[key][field] != master_row[field]
                for field in ("scope", "media_type", "signal_type")
            ):
                raise ValueError("catalog_master_invalid")
            if key != request.master and rows[key]["variant_count"]:
                raise ValueError("catalog_master_invalid")
    for key, row in selected.items():
        if action in {"classify", "ungroup"}:
            if (
                request.classification is None
                or action == "classify"
                and rows[key].get("master_identity")
            ):
                raise ValueError("catalog_variant_override")
            row.update(classification=request.classification, master_key=None)
        if action == "group":
            if key == request.master:
                if request.classification is not None:
                    row["classification"] = request.classification
            else:
                assert request.master is not None
                row.update(classification=None, master_key=rows[request.master]["key"])
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


def detail(state: Any, catalog: Any, key: str, after: str = "") -> dict[str, Any]:
    from .managed_catalog import definition, identity
    from .registry import RegistryConfig
    from .title_sync import current_mapping, keys

    rows = all_rows(state, catalog)
    row = rows.get(key)
    if row is None:
        raise ValueError("catalog_page_invalid")
    variants = [
        item
        for ident, item in sorted(rows.items())
        if item.get("master_identity") == key and ident > after
    ]
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
