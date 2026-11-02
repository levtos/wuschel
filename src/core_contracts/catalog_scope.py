"""Explicit, one-time scope repair through the existing catalog writer."""

import copy
from collections import defaultdict
from typing import Any
from uuid import UUID

from .catalog_care import fields
from .managed_catalog import (
    affected_contracts,
    effective,
    head,
    head_key,
    identity,
    invalidate,
    prepare,
)
from .model import canonical, checksum
from .text_rules import normalize_key
from .title_sync import ImportWrite, current_mapping

RECEIPT = "catalog_scope_repair:v1"
LIMIT = 100_000


def scopes(config: Any, catalog_id: str, version: int) -> set[str | None]:
    result: set[str | None] = set()

    def visit(value: Any, parameters: dict[str, Any]) -> None:
        if isinstance(value, dict):
            if (
                value.get("op") == "managed_catalog_lookup"
                and value.get("catalog_id") == catalog_id
                and value.get("definition_version") == version
            ):
                scope = value.get("scope", {})
                resolved = scope.get("value") if scope.get("op") == "literal" else None
                if scope.get("op") == "param":
                    resolved = parameters.get(scope.get("name"))
                result.add(resolved if isinstance(resolved, str) and resolved.strip() else None)
            for child in value.values():
                visit(child, parameters)
        elif isinstance(value, list):
            for child in value:
                visit(child, parameters)

    if config:
        for contract in config.contracts:
            for definition in config.definitions:
                if (definition.type_id, definition.type_version) == (
                    contract.type_id,
                    contract.type_version,
                ):
                    visit(definition.model_dump(mode="json", by_alias=True), contract.parameters)
    return result


def device_scope(config: Any, catalog_id: str, version: int) -> str | None:
    values = scopes(config, catalog_id, version)
    return next(iter(values)) if len(values) == 1 and None not in values else None


def validate_target(config: Any, target: Any) -> None:
    values = scopes(config, target.catalog_id, target.definition_version)
    if values and (target.scope == "default" or target.scope not in values):
        raise ValueError("title_sync_mapping_invalid")


def telemetry(*rows: dict[str, Any]) -> dict[str, Any]:
    found = [row for row in rows if row and "first_seen" in row]
    if not found:
        return {}
    from datetime import datetime

    return {
        "seen_count": sum(row["seen_count"] for row in found),
        "first_seen": min((row["first_seen"] for row in found), key=datetime.fromisoformat),
        "last_seen": max((row["last_seen"] for row in found), key=datetime.fromisoformat),
    }


def resolution(
    key: str, row: dict[str, Any], reason: str, before: Any, after: Any
) -> dict[str, Any]:
    return {
        "identity": key,
        "key": row["key"],
        "display_name": row.get("display_name", ""),
        "reason": reason,
        "master_before": before,
        "master_after": after,
    }


def resolve_links(
    changes: dict[str, Any],
    entries: dict[str, Any],
    sources: dict[str, Any],
    origins: dict[str, str],
    catalog: Any,
) -> list[dict[str, Any]]:
    """Keep a one-level graph; unresolved links become explicit standalone entries."""
    projected = {**entries, **changes}
    findings = []
    # Break an imported edge before a preserved live edge if merging creates a cycle.
    for key in sorted(
        projected, key=lambda item: (bool(sources.get(item, {}).get("master_key")), item)
    ):
        row = projected[key]
        if row.get("tombstone") or not row.get("master_key"):
            continue
        seen = {key}
        parent_name = row["master_key"]
        reason = "master_flattened"
        parent = None
        while parent_name:
            parent_id = identity(row["scope"], row["media_type"], row["signal_type"], parent_name)
            if parent_id in seen:
                reason = "cyclic_master_standalone"
                break
            seen.add(parent_id)
            parent = projected.get(parent_id)
            if not parent or parent.get("tombstone"):
                reason = "missing_master_standalone"
                break
            if not parent.get("master_key"):
                break
            parent_name = parent["master_key"]
        if reason != "master_flattened":
            value = row.get("classification")
            for original in dict.fromkeys((key, origins.get(key, key))):
                if original not in entries:
                    continue
                old_value, complete, _ = effective(entries, original, catalog)
                if complete:
                    value = old_value
                    break
            updated = {**fields(row), "master_key": None, "classification": value}
        elif parent is not None and parent["key"] != row["master_key"]:
            updated = {**fields(row), "master_key": parent["key"], "classification": None}
        else:
            continue
        changes[key] = projected[key] = updated
        findings.append(resolution(key, updated, reason, row["master_key"], updated["master_key"]))
    return findings


def build(state: Any, config: Any, clear_pc: bool) -> dict[str, Any]:
    groups = []
    sights = copy.deepcopy(state.tables["catalog_sighting"])
    mapping = copy.deepcopy(current_mapping(state)["mapping"])
    total = 0
    for catalog in config.catalogs if config else ():
        if catalog.kind != "managed":
            continue
        scope = device_scope(config, catalog.catalog_id, catalog.version)
        current = head(state, catalog)
        ck = head_key(catalog)
        entries = current["entries"]
        changes: dict[str, Any] = {}
        family_classes: list[tuple[str, str]] = []
        findings: list[dict[str, Any]] = []
        origins: dict[str, str] = {}
        moved = collisions = removed = 0
        clearing = clear_pc and scope == "pc"
        sources = {key: copy.deepcopy(row) for key, row in entries.items() if not row["tombstone"]}
        live_parents = {
            row.get("master_identity") for row in sources.values() if row.get("master_identity")
        }
        for row in sights.values():
            if row["catalog"] == ck and (
                clearing or not entries.get(row["identity"], {}).get("tombstone")
            ):
                sources.setdefault(
                    row["identity"],
                    copy.deepcopy(entries.get(row["identity"]) or {**row, "tombstone": False}),
                )
        for key, row in sources.items():
            empty = not normalize_key(row["key"])
            if clearing or empty:
                # Old empty sightings were never content entries. Remove their projection only.
                if key in entries and not empty:
                    changes[key] = {**fields(row), "tombstone": True}
                sights.pop(ck + ":" + key, None)
                removed += 1
                continue
            if scope in {None, "default"} or row["scope"] != "default":
                continue
            target = identity(scope, row["media_type"], row["signal_type"], row["key"])
            origins[target] = key
            live = sources.get(target, {})
            merged = {**fields(row), "scope": scope}
            if live:
                collisions += 1
                for field in ("display_name", "reviewed_at", "hidden_at"):
                    if live.get(field):
                        merged[field] = live[field]
                merged["note"] = "\n".join(
                    dict.fromkeys(note for note in (row.get("note"), live.get("note")) if note)
                )
                if live.get("master_key"):
                    if row.get("master_key") and row["master_key"] != live["master_key"]:
                        findings.append(
                            resolution(
                                target, merged, "live_master", row["master_key"], live["master_key"]
                            )
                        )
                    merged.update(master_key=live["master_key"], classification=None)
                elif target in live_parents and row.get("master_key"):
                    value, complete, _ = effective(entries, key, catalog)
                    merged.update(master_key=None, classification=value if complete else None)
                    findings.append(
                        resolution(target, merged, "live_master", row["master_key"], None)
                    )
                elif live.get("classification") is not None:
                    if row.get("master_key"):
                        family_classes.append((target, live["classification"]))
                    else:
                        merged.update(classification=live["classification"], master_key=None)
                if live.get("tc_metadata") and not merged.get("tc_metadata"):
                    merged["tc_metadata"] = copy.deepcopy(live["tc_metadata"])
            if key in entries:
                changes[key] = {**fields(row), "tombstone": True}
            changes[target] = merged
            old_sighting = sights.pop(ck + ":" + key, {})
            live_sighting = sights.get(ck + ":" + target, {})
            counts = telemetry(old_sighting, live_sighting)
            if metadata := merged.get("tc_metadata"):
                combined = telemetry(
                    (row.get("tc_metadata") or {}).get("telemetry") or {},
                    (live.get("tc_metadata") or {}).get("telemetry") or {},
                    old_sighting,
                    live_sighting,
                )
                if combined:
                    metadata["telemetry"] = combined
            if counts:
                sights[ck + ":" + target] = {
                    **(live_sighting or old_sighting),
                    **counts,
                    "identity": target,
                    "scope": scope,
                }
            moved += 1
        families: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for target, value in family_classes:
            assert scope is not None
            variant = changes[target]
            master = identity(
                scope, variant["media_type"], variant["signal_type"], variant["master_key"]
            )
            families[master].append((target, value))
        for master, members in families.items():
            master_row = changes.get(master) or fields(entries.get(master, {}))
            live_master = sources.get(master, {})
            values = {value for _, value in members}
            incompatible = (
                not master_row
                or master_row.get("tombstone")
                or master_row.get("master_key")
                or len(values) != 1
                or (
                    live_master.get("classification") is not None
                    and live_master["classification"] not in values
                )
            )
            if incompatible:
                for target, value in members:
                    variant = changes[target]
                    reason = (
                        "missing_master_standalone"
                        if not master_row or master_row.get("tombstone")
                        else "live_class_standalone"
                    )
                    changes[target] = {**variant, "master_key": None, "classification": value}
                    findings.append(
                        resolution(target, variant, reason, variant["master_key"], None)
                    )
            else:
                changes[master] = {**master_row, "classification": next(iter(values))}
        findings.extend(resolve_links(changes, entries, sources, origins, catalog))
        if changes or moved or removed:
            groups.append(
                {
                    "catalog_id": catalog.catalog_id,
                    "definition_version": catalog.version,
                    "expected": current["revision"],
                    "scope": scope,
                    "entries": list(changes.values()),
                    "moved": moved,
                    "collisions": collisions,
                    "removed": removed,
                    "conflicts": len(findings),
                    "findings": findings,
                }
            )
            total += len(changes)
        for target in mapping["mappings"].values():
            if (target["catalog_id"], target["definition_version"], target["scope"]) == (
                catalog.catalog_id,
                catalog.version,
                "default",
            ) and scope not in {None, "default"}:
                target["scope"] = scope
    if total > LIMIT or len(canonical([groups, sights]).encode()) > 50 * 1024 * 1024:
        raise ValueError("catalog_expansion_limit")
    return {"groups": groups, "sightings": sights, "mapping": mapping}


def basis(state: Any, clear_pc: bool) -> str:
    return checksum(
        [
            state.active_revision,
            state.tables["catalog_head"],
            state.tables["catalog_sighting"],
            current_mapping(state),
            clear_pc,
        ]
    )


async def process(
    runtime: Any, state: Any, operation: str, body: dict[str, Any], now: Any
) -> dict[str, Any]:
    request_id = (
        str(UUID(body["request_id"])) if operation == "catalog_scope_apply" else str(UUID(int=0))
    )
    request_digest = checksum(body)
    if operation == "catalog_scope_apply" and (
        record := await runtime.store.get("catalog_request", request_id)
    ):
        if record.get("digest") != request_digest:
            raise ValueError("catalog_request_conflict")
        return copy.deepcopy(record["result"])
    previous = await runtime.store.get("catalog_request", RECEIPT)
    if previous:
        return copy.deepcopy(previous["result"])
    if type(body.get("clear_pc", True)) is not bool:
        raise ValueError("catalog_page_invalid")
    clear_pc = body.get("clear_pc", True)
    if (
        type(body.get("expected_active_revision")) is not int
        or body["expected_active_revision"] != state.active_revision
    ):
        raise ValueError("active_revision_conflict")
    config = runtime._config(state)
    if config is None:
        raise ValueError("catalog_definition_missing")
    plan = build(state, config, clear_pc)
    digest = checksum([basis(state, clear_pc), plan])
    result = {
        "complete": False,
        "plan_digest": digest,
        "clear_pc": clear_pc,
        "catalogs": [
            {k: v for k, v in group.items() if k not in {"entries", "expected"}}
            for group in plan["groups"]
        ],
    }
    previewing = operation == "catalog_scope_preview"
    if not previewing and (body.get("confirmed") is not True or body.get("plan_digest") != digest):
        raise ValueError("catalog_content_conflict")
    from .catalog_models import EntryChange

    affected: set[str] = set()
    latches = copy.deepcopy(runtime.latches)
    for group in plan["groups"]:
        if not group["entries"]:
            continue
        request = ImportWrite.model_validate(
            {
                "catalog_id": group["catalog_id"],
                "definition_version": group["definition_version"],
                "expected_active_revision": state.active_revision,
                "expected_content_revision": group["expected"],
                "request_id": request_id,
                "reason": "Katalogbereiche korrigieren",
                "changes": tuple(EntryChange.model_validate(row) for row in group["entries"]),
            }
        )
        prepared = prepare(state, config, request, body.get("_actor"), now, expanded_limit=LIMIT)
        if not previewing:
            affected.update(affected_contracts(state, prepared))
            latches = invalidate(state, prepared, latches)
    if previewing:
        return result
    state.tables["catalog_sighting"] = plan["sightings"]
    saved = current_mapping(state)
    if saved["mapping"] != plan["mapping"]:
        version = saved["version"] + 1
        state.tables["title_sync_mapping"][str(version)] = {
            "version": version,
            "mapping": plan["mapping"],
            "at": now.isoformat(),
        }
    # A removed PC playback must be able to appear on its next real start.
    cleared = {
        head_key(c)
        for c in config.catalogs
        if c.kind == "managed"
        and clear_pc
        and device_scope(config, c.catalog_id, c.version) == "pc"
    }
    for key, tracker in state.tables["service_state"].items():
        if (
            key.startswith("catalog_playback:")
            and tracker.get("pending", {}).get("catalog") in cleared
        ):
            tracker.pop("pending", None)
    if affected and runtime._evaluate(
        state, config, origin="revision", initial_latches=latches, catalog_affected=affected
    ):
        runtime.pending_latches = None
        runtime.pending_refresh.clear()
        raise ValueError("catalog_evaluation_failed")
    result.update(complete=True, request_id=request_id, publication_seq=state.publication_seq)
    state.tables["catalog_request"][request_id] = {"digest": request_digest, "result": result}
    state.tables["catalog_request"][RECEIPT] = {
        "result": result,
        "at": now.isoformat(),
        "actor": body.get("_actor"),
    }
    await runtime._commit(state, publication=bool(affected))
    return result
