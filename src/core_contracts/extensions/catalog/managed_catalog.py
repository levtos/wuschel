"""Transaction-local append-only catalog maintenance and evidence-preserving lookup."""

import copy
from typing import TYPE_CHECKING, Any

from core_contracts import slots
from core_contracts.model import checksum, semantic_fingerprint
from core_contracts.persistence import State
from core_contracts.quality import FieldValue
from core_contracts.registry import RegistryConfig

if TYPE_CHECKING:
    from core_contracts.runtime import Runtime


from core_contracts.quality import ConfigurationHint, ConfigurationStatus, derive
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange, ManagedCatalog
from core_contracts.text_rules import normalize_key


def definition(config: Any, catalog_id: str, version: int) -> ManagedCatalog:
    item = (
        next(
            (c for c in config.catalogs if c.catalog_id == catalog_id and c.version == version),
            None,
        )
        if config
        else None
    )
    if not isinstance(item, ManagedCatalog):
        raise ValueError("catalog_definition_missing")
    return item


def head_key(catalog: ManagedCatalog) -> str:
    return checksum([catalog.catalog_id, catalog.version])


def definition_digest(catalog: ManagedCatalog) -> str:
    document = catalog.model_dump(mode="json", exclude={"lifecycle", "reactivation_note"})
    if catalog.class_assignments:
        document["assigned_classes"] = {
            key: semantic_fingerprint(definition.model_dump(mode="json"))
            for key, definition in catalog.assigned_classes.items()
        }
    return checksum(document)


def identity(scope: str, media_type: str, signal_type: str, key: str) -> str:
    return checksum([scope, media_type, signal_type, normalize_key(key)])


def head(state: State, catalog: ManagedCatalog) -> dict[str, Any]:
    return state.tables["catalog_head"].get(
        head_key(catalog),
        {
            "catalog_id": catalog.catalog_id,
            "definition_version": catalog.version,
            "definition_digest": definition_digest(catalog),
            "revision": 0,
            "entries": {},
        },
    )


def discovered_entries(state: Any, catalog: ManagedCatalog) -> dict[str, Any]:
    """Virtual unclassified hierarchy from stable sightings; saved care wins.

    Discovery never creates a content revision or a classification. A subsequent
    explicit care request persists the selected rows through the ordinary writer.
    """
    entries: dict[str, Any] = head(state, catalog)["entries"]
    sightings = [
        row
        for row in state.tables["catalog_sighting"].values()
        if row["catalog"] == head_key(catalog)
    ]
    parents = {row.get("master_identity") for row in sightings if row.get("master_key")}
    discovered = [
        row
        for row in sightings
        if row.get("master_key") or row.get("hierarchy_master") or row["identity"] in parents
    ]
    if not discovered:
        return entries
    result = dict(entries)
    for row in discovered:
        result.setdefault(
            row["identity"],
            {
                **row,
                "classification": None,
                "master_key": row.get("master_key"),
                "master_identity": row.get("master_identity"),
                "note": "",
                "tombstone": False,
            },
        )
    return result


def selected_row(entries: dict[str, Any], key: str) -> tuple[dict[str, Any], str | None]:
    row = entries.get(key)
    if not row or row["tombstone"]:
        return {}, None
    master = row.get("master_identity")
    selected = entries.get(master) if master else row
    if not selected or selected["tombstone"] or selected.get("master_identity"):
        raise ValueError("catalog_master_invalid")
    return selected, master


LEGACY_DIMENSIONS = {
    "media.ps5": "sound_mode",
    "media.pc": "sound_mode",
    "media.appletv": "content",
    "media.appletv_titles": "content",
    "media.stash": "content",
    "media.tv_device": "tv_device",
    "media.tv_activity": "tv_activity",
}


def effective(
    entries: dict[str, Any], key: str, catalog: ManagedCatalog, class_key: str | None = None
) -> tuple[Any, bool, str | None]:
    selected, master = selected_row(entries, key)
    values = selected.get("class_values", {})
    defaults = catalog.class_defaults()
    if class_key is not None:
        return values.get(class_key, defaults[class_key]), class_key in values, master
    if dimension := LEGACY_DIMENSIONS.get(catalog.catalog_id):
        if dimension in defaults:
            value = values.get(dimension, defaults[dimension])
            if value not in catalog.classification_schema.values:
                from .interface_upgrade import CLASS_CODES

                value = next(
                    (
                        old
                        for old, new in CLASS_CODES.items()
                        if new == value and old in catalog.classification_schema.values
                    ),
                    catalog.unassigned_value,
                )
            return value, dimension in values, master
    if catalog.catalog_id == "media.homepods" and {"damped", "volume_offset"} <= defaults.keys():
        damped = values.get("damped", defaults["damped"])
        offset = values.get("volume_offset", defaults["volume_offset"])
        scalar = (
            "mute" if damped else "boost" if offset > 0 else "quieter" if offset < 0 else "normal"
        )
        # Schema versions before the interface cutover use the legacy German values.
        scalar = (
            {"mute": "stumm", "quieter": "leiser"}.get(scalar, scalar)
            if scalar not in catalog.classification_schema.values
            else scalar
        )
        return scalar, {"damped", "volume_offset"} <= values.keys(), master
    value = selected.get("classification")
    return (value or catalog.unassigned_value), value is not None, master


def class_view(entries: dict[str, Any], key: str, catalog: ManagedCatalog) -> dict[str, Any]:
    if not catalog.class_assignments:
        return {}
    selected, master = selected_row(entries, key)
    values = selected.get("class_values", {})
    return {
        "effective_class_values": {**catalog.class_defaults(), **values},
        "class_origins": {
            name: ("inherited" if master else "explicit") if name in values else "default"
            for name in catalog.assigned_classes
        },
    }


def semantic(
    entries: dict[str, Any], key: str, catalog: ManagedCatalog, class_key: str | None = None
) -> str:
    # A different parent with the same effective class/completeness changes no basis.
    value, complete, _master = effective(entries, key, catalog, class_key)
    return checksum([value, complete, bool(entries.get(key, {}).get("tombstone"))])


def trace_key(key: str, class_key: str | None) -> str:
    return f"{key}.{class_key}" if class_key else key


def token(catalog: ManagedCatalog, key: str, digest: str) -> str:
    return f"catalog:{head_key(catalog)}:{key}:{digest}"


def dependencies(node: dict[str, Any], values: list[FieldValue]) -> bool:
    """Remember historical branches for stateful slots, compare before using old state."""
    before = dict(node.get("catalog_dependencies", {}))
    current = {
        e.rsplit(":", 1)[0]: e.rsplit(":", 1)[1]
        for value in values
        for e in value.evidence
        if e.startswith("catalog:")
    }
    changed = any(key in before and before[key] != value for key, value in current.items())
    before.update(current)
    if len(before) > 4096:
        raise ValueError("catalog_trace_limit")
    node["catalog_dependencies"] = before
    return changed


def suspend_machine(node: dict[str, Any]) -> None:
    if "machine" in node:
        node["catalog_machine_pending"] = node.pop("machine")


def lookup(ctx: Any, rule: Any, args: list[FieldValue]) -> FieldValue:
    if getattr(rule, "profile", None) == "apple_tv_title":
        from core_contracts.extensions.catalog.apple_tv_catalog import lookup as title_lookup

        return title_lookup(ctx, rule, args)
    if any(not value.usable(ctx.now) for value in args):
        return derive(None, args, ctx.now)
    if getattr(rule, "profile", None) == "stash_slot":
        from core_contracts.extensions.catalog.stash_catalog import lookup as slot_lookup

        return slot_lookup(ctx, rule, args)
    document = ctx.state.tables["registry_revision"][str(ctx.state.active_revision)]["config"]

    catalog = definition(
        RegistryConfig.model_validate(document), rule.catalog_id, rule.definition_version
    )
    scope, media, signal, raw = (str(value.value) for value in args)
    key = identity(scope, media, signal, raw)
    current = head(ctx.state, catalog)
    if media == "music" and signal == "title" and key not in current["entries"]:
        from core_contracts.extensions.catalog.catalog_families import music_hierarchy
        from core_contracts.extensions.catalog.catalog_hierarchy import lookup as family_lookup

        if hierarchy := music_hierarchy(raw):
            return family_lookup(ctx, rule, args, raw, hierarchy)
    class_key = getattr(rule, "class_key", None)
    value, complete, master = effective(current["entries"], key, catalog, class_key)
    digest = semantic(current["entries"], key, catalog, class_key)
    dependency = trace_key(key, class_key)
    result = derive(value, args, ctx.now)
    result = result.model_copy(
        update={"evidence": (*result.evidence, token(catalog, dependency, digest))}
    )
    ctx.node.setdefault("catalog_lookups", {})[head_key(catalog) + ":" + dependency] = {
        "catalog": head_key(catalog),
        "key": key,
        **({"class_key": class_key} if class_key else {}),
        "content_revision": current["revision"],
        "master": master,
        "semantic_digest": digest,
        "complete": complete,
    }
    if not complete:
        hint = ConfigurationHint(code="catalog_assignment_missing")
        result = result.model_copy(
            update={
                "configuration": ConfigurationStatus(
                    complete=False, hints=tuple(dict.fromkeys((*result.configuration.hints, hint)))
                )
            }
        )
    # Only offer a catalog candidate. Contract evaluation remains immediate.
    evidence = tuple(e for arg in args for e in arg.evidence if not e.startswith("catalog:"))
    from core_contracts.extensions.catalog.catalog_playback import offer

    offer(
        ctx,
        catalog,
        scope,
        media,
        signal,
        raw,
        evidence,
        min((value.valid_until for value in args if value.valid_until), default=None),
    )
    return result


def prepare(
    state: Any,
    config: Any,
    request: CatalogWrite,
    actor: Any,
    now: Any,
    *,
    expanded_limit: int = 1000,
    force_revision: bool = False,
) -> dict[str, Any]:
    if request.expected_active_revision != state.active_revision:
        raise ValueError("active_revision_conflict")
    catalog = definition(config, request.catalog_id, request.definition_version)
    if catalog.lifecycle == "retired":
        raise ValueError("catalog_retired")
    old = head(state, catalog)
    if old["revision"] != request.expected_content_revision:
        raise ValueError("catalog_content_conflict")
    if old["definition_digest"] != definition_digest(catalog):
        raise ValueError("catalog_definition_conflict")
    # Existing entry revisions are immutable; only direct changes create new rows.
    entries = dict(old["entries"])
    before_view = discovered_entries(state, catalog)
    changes = list(request.changes)
    requested = {identity(row.scope, row.media_type, row.signal_type, row.key) for row in changes}
    # Every explicit write path (including the single-entry editor) saves a
    # required discovered studio atomically, without inventing a classification.
    for change in request.changes:
        if change.master_key:
            parent = identity(
                change.scope, change.media_type, change.signal_type, change.master_key
            )
            if parent not in entries and parent not in requested and parent in before_view:
                changes.append(
                    EntryChange.model_validate(
                        {
                            key: value
                            for key, value in before_view[parent].items()
                            if key in EntryChange.model_fields
                        }
                    )
                )
                requested.add(parent)
    if len(changes) > expanded_limit:
        raise ValueError("catalog_expansion_limit")
    direct: set[str] = set()
    for change in changes:
        row = change.model_dump(mode="json")
        key = identity(change.scope, change.media_type, change.signal_type, change.key)
        for optional in (
            "reviewed_at",
            "hidden_at",
            "tc_metadata",
            "display_metadata",
            "catalog_metadata",
        ):
            if optional not in change.model_fields_set and optional in entries.get(key, {}):
                row[optional] = entries[key][optional]
        if not normalize_key(change.key):
            raise ValueError("catalog_key_empty")
        if key in direct:
            raise ValueError("catalog_duplicate_change")
        direct.add(key)
        if (
            change.classification is not None
            and change.classification not in catalog.classification_schema.values
        ):
            raise ValueError("catalog_class_invalid")
        if "class_values" not in change.model_fields_set and not change.master_key:
            if old_values := entries.get(key, {}).get("class_values"):
                row["class_values"] = old_values
        catalog.validate_classes(row.get("class_values", {}))
        if change.master_key and (change.classification is not None or row.get("class_values")):
            raise ValueError("catalog_variant_override")
        if catalog.class_assignments and change.classification is not None:
            raise ValueError("catalog_named_classes_required")
        row["master_identity"] = (
            identity(change.scope, change.media_type, change.signal_type, change.master_key)
            if change.master_key
            else None
        )
        row["normalized_key"] = normalize_key(change.key)
        entries[key] = row
    after_view = {**before_view, **entries} if before_view is not old["entries"] else entries
    for key in direct:
        effective(entries, key, catalog)
    for key, row in after_view.items():
        if not row["tombstone"]:
            if row.get("master_identity") == key:
                raise ValueError("catalog_master_invalid")
            effective(after_view, key, catalog)
    # A direct change can affect only itself and children of an old/new master.
    # Still validate the whole graph above, including deletions and cycles.
    candidates = direct | {
        key
        for rows in (before_view, after_view)
        for key, row in rows.items()
        if row.get("master_identity") in direct
    }
    affected_traces = {
        trace_key(key, class_key)
        for key in candidates
        for class_key in (None, *catalog.assigned_classes)
        if semantic(after_view, key, catalog, class_key)
        != semantic(before_view, key, catalog, class_key)
    }
    affected = {item.partition(".")[0] for item in affected_traces}
    if len(affected) > expanded_limit:
        raise ValueError("catalog_expansion_limit")
    changed = {key for key in direct if entries[key] != old["entries"].get(key)}
    revision = old["revision"] + bool(changed or force_revision)
    if changed or force_revision:
        revision_key = f"{head_key(catalog)}:{revision}"
        state.tables["catalog_content_revision"][revision_key] = {
            "catalog": head_key(catalog),
            "revision": revision,
            "parent_revision": old["revision"],
            "request_id": request.request_id,
            "actor": actor,
            "at": now.isoformat(),
            "reason": request.reason,
            "changed": sorted(changed),
            "affected": sorted(affected),
        }
        for key in changed:
            state.tables["catalog_entry_revision"][revision_key + ":" + key] = {
                "catalog": head_key(catalog),
                "revision": revision,
                "identity": key,
                "entry": entries[key],
            }
        state.tables["catalog_head"][head_key(catalog)] = {
            **old,
            "revision": revision,
            "entries": entries,
        }
    return {
        "content_revision": revision,
        "changed": len(changed),
        "affected_keys": sorted(affected),
        **({"affected_traces": sorted(affected_traces)} if catalog.class_assignments else {}),
        "catalog": head_key(catalog),
    }


def invalidate(
    state: Any,
    result: dict[str, Any],
    latches: dict[str, Any],
    effects: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    latches = copy.deepcopy(latches)
    traces = result.get("affected_traces", result["affected_keys"])
    prefixes = {f"catalog:{result['catalog']}:{key}" for key in traces}
    selected = affected_contracts(state, result)
    state.detach_rows("node_state", selected)
    for contract_id in state.tables["node_state"]:
        if contract_id not in selected:
            continue
        root = state.tables["node_state"][contract_id]

        def report(storage: str, slot: str) -> None:
            if effects is not None:
                effects.append(
                    {
                        "contract_id": contract_id,
                        "storage": storage,
                        "slot": slot,
                        "case": "b",
                        "reason": "catalog_classification_changed",
                    }
                )

        if prefixes.intersection(root.get("catalog_machine", {}).get("catalog_dependencies", {})):
            if root.get("machine"):
                report("machine", "")
            suspend_machine(root)
        for slot, node in [
            (None, root),
            *root.get("rules", {}).items(),
            *root.get("catalog_buckets", {}).items(),
        ]:
            if prefixes.intersection(node.get("catalog_dependencies", {})):
                if any(name in node for name in ("temporal", "age_input", "edge_active")):
                    report("rules" if slot is not None else "temporal", slot or "")
                latch_key = contract_id if slot is None else f"{contract_id}:{slot}"
                if latch_key in latches:
                    report("latch", slot or "")
                for name in (
                    "temporal",
                    "restore_pending",
                    "age_input",
                    "age_value",
                    "edge_active",
                ):
                    node.pop(name, None)
                suspend_machine(node)
                if "catalog_machine_pending" not in node:
                    node.pop("catalog_dependencies", None)
                latches.pop(contract_id if slot is None else f"{contract_id}:{slot}", None)
    return latches


def affected_contracts(state: Any, result: dict[str, Any]) -> set[str]:
    traces = set(result.get("affected_traces", result["affected_keys"]))
    prefixes = {f"catalog:{result['catalog']}:{key}" for key in traces}
    return {
        contract_id
        for contract_id, root in state.tables["node_state"].items()
        if any(
            item["catalog"] == result["catalog"]
            and trace_key(item["key"], item.get("class_key")) in traces
            for item in root.get("catalog_lookups", {}).values()
        )
        or any(
            prefixes.intersection(node.get("catalog_dependencies", {}))
            for node in [
                root,
                root.get("catalog_machine", {}),
                *root.get("rules", {}).values(),
                *root.get("catalog_buckets", {}).values(),
            ]
        )
    }


def validate_heads(state: Any, config: Any) -> None:
    if config is None:
        return
    for catalog in config.catalogs:
        if not isinstance(catalog, ManagedCatalog):
            continue
        current = head(state, catalog)
        if current["definition_digest"] != definition_digest(catalog) or current["revision"] < 0:
            raise ValueError("catalog_restore_incompatible")
        for key, row in current["entries"].items():
            if key != identity(row["scope"], row["media_type"], row["signal_type"], row["key"]):
                raise ValueError("catalog_restore_incompatible")
            if (
                row.get("classification") is not None
                and row["classification"] not in catalog.classification_schema.values
            ):
                raise ValueError("catalog_restore_incompatible")
            catalog.validate_classes(row.get("class_values", {}))
            if row.get("class_values") and row.get("master_identity"):
                raise ValueError("catalog_restore_incompatible")
            if catalog.class_assignments and row.get("classification") is not None:
                raise ValueError("catalog_restore_incompatible")
            effective(current["entries"], key, catalog)


def entry_view(state: State, catalog: ManagedCatalog, key: str) -> dict[str, Any]:
    current = head(state, catalog)
    entries = discovered_entries(state, catalog)
    row = entries.get(key)
    value, complete, master = effective(entries, key, catalog)
    return {
        "content_revision": current["revision"],
        "active_revision": state.active_revision,
        "entry": row,
        **class_view(entries, key, catalog),
        "effective_classification": value,
        "complete": complete,
        "master": master,
    }


async def historical_entries(store: Any, catalog: ManagedCatalog, revision: int) -> dict[str, Any]:
    if revision < 0:
        raise ValueError("revision_not_found")
    if revision and not await store.get(
        "catalog_content_revision", f"{head_key(catalog)}:{revision}"
    ):
        raise ValueError("revision_not_found")
    return dict(await store.managed_entries(head_key(catalog), revision))


async def verify_stored_heads(store: Any, state: State, config: Any) -> None:
    if config is None:
        return
    for catalog in config.catalogs:
        if isinstance(catalog, ManagedCatalog):
            current = head(state, catalog)
            history = await historical_entries(store, catalog, current["revision"])
            if history != current["entries"]:
                raise ValueError("catalog_restore_incompatible")


def page(
    state: Any,
    catalog: ManagedCatalog,
    *,
    query: str = "",
    view: str = "all",
    after: str = "",
    limit: int = 100,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    filters = filters or {}
    if filters.keys() - {"scope", "media_type", "signal_type"} or any(
        len(value) > 128 for value in filters.values()
    ):
        raise ValueError("catalog_page_invalid")
    if not 1 <= limit <= 100 or len(query) > 1024 or view not in {"all", "unassigned", "masters"}:
        raise ValueError("catalog_page_invalid")
    current = head(state, catalog)
    entries = discovered_entries(state, catalog)
    rows = {key: {"identity": key, **row} for key, row in entries.items() if not row["tombstone"]}
    for sighting in state.tables["catalog_sighting"].values():
        if sighting["catalog"] == head_key(catalog):
            key = sighting["identity"]
            rows.setdefault(
                key,
                {
                    **sighting,
                    "classification": None,
                    "master_key": None,
                    "display_name": "",
                    "note": "",
                },
            )
            rows[key].update(
                {field: sighting[field] for field in ("first_seen", "last_seen", "seen_count")}
            )
    selected = []
    for key, row in sorted(rows.items()):
        if any(value and row.get(name) != value for name, value in filters.items()):
            continue
        value, complete, master = effective(entries, key, catalog)
        searchable = " ".join(
            [row["key"], row.get("display_name", ""), *(row.get("display_metadata") or {}).values()]
        )
        if key <= after or (query and normalize_key(query) not in normalize_key(searchable)):
            continue
        if view == "unassigned" and complete or view == "masters" and master:
            continue
        selected.append(
            {
                **row,
                **class_view(entries, key, catalog),
                "effective_classification": value,
                "complete": complete,
                "semantic_digest": semantic(entries, key, catalog),
            }
        )
    return {
        "items": selected[:limit],
        "content_revision": current["revision"],
        "next_cursor": selected[limit - 1]["identity"] if len(selected) > limit else None,
    }


async def restore_heads(runtime: Runtime, state: State, context: Any) -> None:
    validate_heads(runtime.state, runtime.config)
    await verify_stored_heads(runtime.store, runtime.state, runtime.config)


slots.RESTORE[10] = slots.Hook(restore_heads, "heads")
