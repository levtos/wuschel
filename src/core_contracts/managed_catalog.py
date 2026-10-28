"""Transaction-local append-only catalog maintenance and evidence-preserving lookup."""

import copy
from typing import Any

from .catalog_models import CatalogWrite, ManagedCatalog
from .model import checksum
from .persistence import State
from .quality import ConfigurationHint, ConfigurationStatus, FieldValue, derive
from .text_rules import normalize_key


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
    return checksum(catalog.model_dump(mode="json", exclude={"lifecycle", "reactivation_note"}))


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


def effective(
    entries: dict[str, Any], key: str, catalog: ManagedCatalog
) -> tuple[str, bool, str | None]:
    row = entries.get(key)
    if not row or row["tombstone"]:
        return catalog.unassigned_value, False, None
    master = row.get("master_identity")
    selected = entries.get(master) if master else row
    if not selected or selected["tombstone"] or selected.get("master_identity"):
        raise ValueError("catalog_master_invalid")
    value = selected.get("classification")
    return (value or catalog.unassigned_value), value is not None, master


def semantic(entries: dict[str, Any], key: str, catalog: ManagedCatalog) -> str:
    # A different parent with the same effective class/completeness changes no basis.
    value, complete, _master = effective(entries, key, catalog)
    return checksum([value, complete, bool(entries.get(key, {}).get("tombstone"))])


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
    if any(not value.usable(ctx.now) for value in args):
        return derive(None, args, ctx.now)
    document = ctx.state.tables["registry_revision"][str(ctx.state.active_revision)]["config"]
    from .registry import RegistryConfig

    catalog = definition(
        RegistryConfig.model_validate(document), rule.catalog_id, rule.definition_version
    )
    scope, media, signal, raw = (str(value.value) for value in args)
    key = identity(scope, media, signal, raw)
    current = head(ctx.state, catalog)
    value, complete, master = effective(current["entries"], key, catalog)
    digest = semantic(current["entries"], key, catalog)
    result = derive(value, args, ctx.now)
    result = result.model_copy(update={"evidence": (*result.evidence, token(catalog, key, digest))})
    ctx.node.setdefault("catalog_lookups", {})[head_key(catalog) + ":" + key] = {
        "catalog": head_key(catalog),
        "key": key,
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
    # Count new source reports only. Revision/tick/restore/replay never count.
    evidence = tuple(e for arg in args for e in arg.evidence if not e.startswith("catalog:"))
    from .evidence import Observation

    eligible = {
        item.reference
        for row in ctx.state.tables["source_observation_current"].values()
        if (item := Observation.model_validate(row)).observation_kind in {"live_change", "report"}
        and not item.mqtt_retained
        and not item.ha_restored
    }
    reports = tuple(e for e in evidence if e in eligible)
    if ctx.origin == "input" and reports:
        sighting_key = head_key(catalog) + ":" + key
        previous = ctx.state.tables["catalog_sighting"].get(sighting_key, {})
        report = checksum(reports)
        if previous.get("report") != report:
            ctx.state.tables["catalog_sighting"][sighting_key] = {
                "catalog": head_key(catalog),
                "identity": key,
                "scope": scope,
                "media_type": media,
                "signal_type": signal,
                "key": raw,
                "first_seen": previous.get("first_seen", ctx.now.isoformat()),
                "last_seen": ctx.now.isoformat(),
                "seen_count": previous.get("seen_count", 0) + 1,
                "report": report,
            }
    return result


def prepare(
    state: Any,
    config: Any,
    request: CatalogWrite,
    actor: Any,
    now: Any,
    *,
    expanded_limit: int = 1000,
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
    entries = copy.deepcopy(old["entries"])
    direct: set[str] = set()
    for change in request.changes:
        row = change.model_dump(mode="json")
        key = identity(change.scope, change.media_type, change.signal_type, change.key)
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
        if change.master_key and change.classification is not None:
            raise ValueError("catalog_variant_override")
        row["master_identity"] = (
            identity(change.scope, change.media_type, change.signal_type, change.master_key)
            if change.master_key
            else None
        )
        row["normalized_key"] = normalize_key(change.key)
        entries[key] = row
    for key, row in entries.items():
        if not row["tombstone"]:
            if row.get("master_identity") == key:
                raise ValueError("catalog_master_invalid")
            effective(entries, key, catalog)
    candidates = set(entries) | set(old["entries"])
    affected = {
        key
        for key in candidates
        if semantic(entries, key, catalog) != semantic(old["entries"], key, catalog)
    }
    if len(affected) > expanded_limit:
        raise ValueError("catalog_expansion_limit")
    changed = {key for key in direct if entries[key] != old["entries"].get(key)}
    revision = old["revision"] + bool(changed)
    if changed:
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
        "catalog": head_key(catalog),
    }


def invalidate(
    state: Any,
    result: dict[str, Any],
    latches: dict[str, Any],
    effects: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    latches = copy.deepcopy(latches)
    prefixes = {f"catalog:{result['catalog']}:{key}" for key in result["affected_keys"]}
    for contract_id, root in state.tables["node_state"].items():

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
    prefixes = {f"catalog:{result['catalog']}:{key}" for key in result["affected_keys"]}
    return {
        contract_id
        for contract_id, root in state.tables["node_state"].items()
        if any(
            item["catalog"] == result["catalog"] and item["key"] in result["affected_keys"]
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
            effective(current["entries"], key, catalog)


def entry_view(state: State, catalog: ManagedCatalog, key: str) -> dict[str, Any]:
    current = head(state, catalog)
    row = current["entries"].get(key)
    value, complete, master = effective(current["entries"], key, catalog)
    return {
        "content_revision": current["revision"],
        "active_revision": state.active_revision,
        "entry": row,
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
    rows = {
        key: {"identity": key, **row}
        for key, row in current["entries"].items()
        if not row["tombstone"]
    }
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
        value, complete, master = effective(current["entries"], key, catalog)
        if key <= after or (query and normalize_key(query) not in normalize_key(row["key"])):
            continue
        if view == "unassigned" and complete or view == "masters" and master:
            continue
        selected.append(
            {
                **row,
                "effective_classification": value,
                "complete": complete,
                "semantic_digest": semantic(current["entries"], key, catalog),
            }
        )
    return {
        "items": selected[:limit],
        "content_revision": current["revision"],
        "next_cursor": selected[limit - 1]["identity"] if len(selected) > limit else None,
    }
