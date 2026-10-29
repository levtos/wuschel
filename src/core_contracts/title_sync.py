"""Pure reconciliation planning and one serialized, atomic catalog operation."""

import copy
from collections import Counter
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from .catalog_models import CatalogWrite, EntryChange, TitleMetadata
from .managed_catalog import (
    affected_contracts,
    definition,
    head,
    head_key,
    identity,
    invalidate,
    prepare,
)
from .model import Model, canonical, checksum
from .title_import import MAX_BYTES, MAX_ENTRIES, plan_title_import, source_identity
from .title_transfer import TargetMapping, classification, context_key, music_forms

MAPPING_LIMIT = 6400


def restore_mapping(current: dict[str, Any], restored: dict[str, Any]) -> None:
    for key, row in current.items():
        if key in restored and canonical(restored[key]) != canonical(row):
            raise ValueError("title_sync_mapping_conflict")
        restored[key] = copy.deepcopy(row)


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "groups"}


def diff_page(runtime: Any, actor: Any, plan_id: str, after: str = "") -> dict[str, Any]:
    plan = runtime.title_sync_plans.get(checksum(actor))
    if not plan or plan["plan_id"] != plan_id:
        raise ValueError("title_sync_plan_conflict")
    rows = []
    for group_id, group in sorted(plan["groups"].items()):
        for key, row in sorted(group["entries"].items()):
            cursor = canonical([group_id, key])
            if cursor > after:
                old = group.get("before", {}).get(key)
                rows.append(
                    {
                        "cursor": cursor,
                        "catalog_id": group["catalog_id"],
                        "before": old,
                        "after": row,
                    }
                )
    rows.sort(key=lambda row: row["cursor"])
    return {"items": rows[:100], "next_cursor": rows[99]["cursor"] if len(rows) > 100 else None}


class Mapping(Model):
    mappings: dict[str, TargetMapping] = Field(default_factory=dict, max_length=MAPPING_LIMIT)
    standalone: tuple[str, ...] = Field(default=(), max_length=MAX_ENTRIES)


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


def standalone_key(row: dict[str, Any], target: TargetMapping) -> str:
    return checksum([source_identity(row), target.catalog_id, target.definition_version])


def keys(row: dict[str, Any], profile: str) -> list[str]:
    if profile != "music":
        return [row["key"]]
    if row.get("title") is not None and row.get("artist") is not None:
        return list(
            dict.fromkeys([canonical([row["artist"], row["title"]]), canonical([row["title"]])])
        )
    return list(music_forms(row["key"]))


def metadata(row: dict[str, Any], previous: dict[str, Any] | None = None) -> TitleMetadata:
    previous = previous or {}
    values = {
        k: row.get(k, previous.get(k))
        for k in ("artist", "title", "album", "app_name", "parent_id", "telemetry")
    }
    old_contexts = {context_key(ctx): ctx for ctx in previous.get("contexts", [])}
    contexts = [
        {
            **ctx,
            "telemetry": ctx.get(
                "telemetry", old_contexts.get(context_key(ctx), {}).get("telemetry")
            ),
        }
        for ctx in row.get("contexts", [])
    ]
    return TitleMetadata.model_validate(
        {
            "source_id": row.get("id", previous.get("source_id")),
            "source_identity": source_identity(row),
            **values,
            "source_class": row["enum"],
            "contexts": contexts,
        }
    )


def plan(
    runtime: Any, state: Any, document: dict[str, Any], body: dict[str, Any]
) -> dict[str, Any]:
    if len(canonical(document).encode()) > MAX_BYTES:
        raise ValueError("title_export_limit")
    if (
        type(body.get("expected_active_revision")) is not int
        or body["expected_active_revision"] != state.active_revision
    ):
        raise ValueError("active_revision_conflict")
    saved = current_mapping(state)
    if "mapping" in body:
        if body.get("expected_mapping_version") != saved["version"]:
            raise ValueError("title_sync_mapping_conflict")
        mapping = Mapping.model_validate(body["mapping"])
    else:
        mapping = Mapping.model_validate(saved["mapping"])
    mapping_doc = mapping.model_dump(mode="json")
    parsed = plan_title_import(document, checksum(document))
    rows = document["entries"]
    valid_rows = [rows[candidate["row"] - 1] for candidate in parsed["candidates"]]
    parents = {source_identity(row): row for row in valid_rows}
    parent_contexts: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for child in valid_rows:
        if child.get("parent_key"):
            parent_contexts.setdefault(source_identity(child, child["parent_key"]), []).extend(
                {"context": c["context"], "source_app": c.get("source_app", "")}
                for c in child.get("contexts", [])
            )
    contexts = {
        context_key(ctx): ctx
        for row in valid_rows
        for ctx in row.get("contexts") or [{"context": "", "source_app": ""}]
    }
    errors = list(parsed["rejected_rows"])
    groups: dict[str, dict[str, Any]] = {}
    source_outcomes: dict[int, set[str]] = {}
    aliases: dict[tuple[str, str], list[dict[str, Any]]] = {}
    count = 0
    size = 0

    def add(
        row: dict[str, Any],
        target: TargetMapping,
        ctx: dict[str, Any],
        number: int,
        dependency: bool = False,
    ) -> str:
        nonlocal count, size
        catalog = definition(runtime._config(state), target.catalog_id, target.definition_version)
        if (
            (target.profile == "game" and row["media_type"] != "game")
            or (target.profile == "music" and row["media_type"] != "music")
            or (target.profile == "private" and row["media_type"] != "video")
        ):
            raise ValueError("title_import_profile")
        group_key = head_key(catalog)
        group = groups.setdefault(
            group_key,
            {
                "catalog_id": catalog.catalog_id,
                "definition_version": catalog.version,
                "expected": head(state, catalog)["revision"],
                "entries": {},
                "before": {},
            },
        )
        own_keys = keys(row, target.profile)
        parent = (
            parents.get(source_identity(row, row["parent_key"])) if row.get("parent_key") else None
        )
        if row.get("parent_id") and (not parent or parent.get("id") != row["parent_id"]):
            raise ValueError("title_sync_parent_invalid")
        independent = standalone_key(row, target) in mapping.standalone
        parent_key = None
        if parent and not independent:
            if parent.get("parent_key") or source_identity(parent) == source_identity(row):
                raise ValueError("catalog_master_invalid")
            parent_ctx: dict[str, Any] = next(
                (
                    item
                    for item in parent.get("contexts", [])
                    if context_key(item) == context_key(ctx)
                ),
                {},
            )
            parent_value = parent_ctx.get("enum_override")
            parent_class = classification(
                target.profile, parent["enum"] if parent_value is None else parent_value
            )
            if (
                ctx.get("enum_override") is not None
                and classification(target.profile, ctx["enum_override"]) != parent_class
            ):
                raise ValueError("title_sync_variant_override")
            parent_key = add(parent, target, parent_ctx, number, dependency=True)
        override = ctx.get("enum_override")
        value = classification(target.profile, row["enum"] if override is None else override)
        primary = own_keys[0]
        ident = identity(target.scope, row["media_type"], row["signal_type"], primary)
        old = head(state, catalog)["entries"].get(ident, {})
        group["before"][ident] = copy.deepcopy(old) or None
        source_meta = metadata(row, old.get("tc_metadata")).model_dump(mode="json")
        change = EntryChange.model_validate(
            {
                "scope": target.scope,
                "media_type": row["media_type"],
                "signal_type": row["signal_type"],
                "key": primary,
                "classification": None if parent_key else value,
                "master_key": parent_key,
                "display_name": row["key"],
                "note": old.get("note", ""),
                "tc_metadata": source_meta,
                "reviewed_at": row.get("reviewed_at"),
                "hidden_at": row.get("hidden_at"),
            }
        ).model_dump(mode="json")
        previous = group["entries"].get(ident)
        if previous is not None and previous != change:
            # A parent closure is identical regardless of traversal order. Distinct context intent is a blocker.
            raise ValueError("title_sync_target_collision")
        if previous is None:
            count += 1
            size += len(canonical(change).encode())
            if count > MAX_ENTRIES or size > MAX_BYTES:
                raise ValueError("title_export_limit")
            group["entries"][ident] = change
        for extra in own_keys[1:]:
            extra_id = identity(target.scope, row["media_type"], row["signal_type"], extra)
            extra_old = head(state, catalog)["entries"].get(extra_id, {})
            if (
                extra_old
                and extra_old.get("tc_metadata", {}).get("source_identity")
                != source_meta["source_identity"]
            ):
                continue
            alias = {
                **change,
                "key": extra,
                "classification": None,
                "master_key": parent_key or primary,
                "note": extra_old.get("note", ""),
                "tc_metadata": {**change["tc_metadata"], "is_alias": True},
            }
            bucket = aliases.setdefault((group_key, extra_id), [])
            if alias not in bucket:
                bucket.append(alias)
        if not dependency:
            stored = {
                k: v for k, v in old.items() if k not in {"normalized_key", "master_identity"}
            }
            outcome = "new" if not old else "unchanged" if stored == change else "changed"
            source_outcomes.setdefault(number, set()).add(outcome)
        return primary

    for candidate in parsed["candidates"]:
        number = candidate["row"]
        row = rows[number - 1]
        inherited_contexts = parent_contexts.get(source_identity(row), [])
        row_contexts = (
            row.get("contexts") or inherited_contexts or [{"context": "", "source_app": ""}]
        )
        for ctx in row_contexts:
            target = mapping.mappings.get(context_key(ctx))
            if target is None:
                errors.append(
                    {"row": number, "context": context_key(ctx), "reasons": ["context_unmapped"]}
                )
                continue
            try:
                add(row, target, ctx, number)
            except (ValueError, KeyError) as error:
                # Never echo submitted values/driver details. Detailed class/raw values remain in the private diff.
                errors.append(
                    {
                        "row": number,
                        "context": context_key(ctx),
                        "key": row["key"],
                        "standalone_key": standalone_key(row, target),
                        "reasons": [
                            str(error)
                            if str(error)
                            in {
                                "title_sync_variant_override",
                                "title_sync_parent_invalid",
                                "catalog_master_invalid",
                                "title_sync_target_collision",
                                "title_import_profile",
                            }
                            else "title_sync_mapping_invalid"
                        ],
                    }
                )
    skipped_aliases = 0
    for (group_key, ident), candidates in aliases.items():
        if ident in groups[group_key]["entries"] or len(candidates) != 1:
            skipped_aliases += 1
            continue
        count += 1
        size += len(canonical(candidates[0]).encode())
        if count > MAX_ENTRIES or size > MAX_BYTES:
            raise ValueError("title_export_limit")
        groups[group_key]["entries"][ident] = candidates[0]
        catalog = definition(
            runtime._config(state),
            groups[group_key]["catalog_id"],
            groups[group_key]["definition_version"],
        )
        groups[group_key]["before"][ident] = copy.deepcopy(
            head(state, catalog)["entries"].get(ident)
        )
    counts: Counter[str] = Counter()
    invalid_rows = {item["row"] for item in errors}
    for number in range(1, len(rows) + 1):
        outcomes = source_outcomes.get(number, set())
        counts[
            "unmappable"
            if number in invalid_rows or not outcomes
            else "new"
            if "new" in outcomes
            else "changed"
            if "changed" in outcomes
            else "unchanged"
        ] += 1
    result: dict[str, Any] = {
        "counts": dict(counts),
        "source_rows": len(rows),
        "target_rows": count,
        "skipped_aliases": skipped_aliases,
        "errors": errors,
        "contexts": [{"key": key, **ctx} for key, ctx in sorted(contexts.items())],
        "mapping": mapping_doc,
        "mapping_version": saved["version"],
        "expected_active_revision": state.active_revision,
        "groups": groups,
        "source_digest": checksum(document),
        "can_apply": not errors,
    }
    probe = state.clone()
    effects: list[dict[str, str]] = []
    affected: set[str] = set()
    changes = 0
    if not errors:
        for group in groups.values():
            request = write_request(group, state.active_revision, str(uuid4()))
            prepared = prepare(
                probe,
                runtime._config(state),
                request,
                None,
                runtime.clock.now_utc(),
                expanded_limit=MAX_ENTRIES,
            )
            affected.update(affected_contracts(state, prepared))
            invalidate(probe, prepared, runtime.latches, effects)
            changes += prepared["changed"]
    result.update(
        changed_targets=changes, affected_contracts=sorted(affected), context_effects=effects
    )
    result["plan_digest"] = checksum(result)
    return result


def write_request(group: dict[str, Any], revision: int, request_id: str) -> ImportWrite:
    return ImportWrite.model_validate(
        {
            "catalog_id": group["catalog_id"],
            "definition_version": group["definition_version"],
            "expected_active_revision": revision,
            "expected_content_revision": group["expected"],
            "request_id": request_id,
            "reason": "Mit Title Classifier abgleichen",
            "changes": list(group["entries"].values()),
        }
    )


async def process(
    runtime: Any, state: Any, operation: str, body: dict[str, Any], now: Any
) -> dict[str, Any]:
    actor = checksum(body.get("_actor"))
    if operation == "title_sync_preview":
        result = plan(runtime, state, body["_snapshot"], body)
        result.update(plan_id=str(uuid4()), at=now.isoformat())
        if not hasattr(runtime, "title_sync_plans"):
            runtime.title_sync_plans = {}
        runtime.title_sync_plans[actor] = result
        return result
    request_id = str(UUID(body["request_id"]))
    digest = checksum({k: body[k] for k in ("plan_id", "plan_digest", "request_id")})
    previous = await runtime.store.get("catalog_request", request_id)
    if previous:
        if previous["digest"] != digest:
            raise ValueError("catalog_request_conflict")
        return dict(previous["result"])
    selected = getattr(runtime, "title_sync_plans", {}).get(actor)
    if (
        not selected
        or body.get("plan_id") != selected["plan_id"]
        or body.get("plan_digest") != selected["plan_digest"]
    ):
        raise ValueError("title_sync_plan_conflict")
    if not selected["can_apply"]:
        raise ValueError("title_sync_mapping_invalid")
    if current_mapping(state)["version"] != selected["mapping_version"]:
        raise ValueError("title_sync_mapping_conflict")
    if state.active_revision != selected["expected_active_revision"]:
        raise ValueError("active_revision_conflict")
    config = runtime._config(state)
    affected: set[str] = set()
    latches = copy.deepcopy(runtime.latches)
    revisions = {}
    changed = 0
    for key, group in selected["groups"].items():
        prepared = prepare(
            state,
            config,
            write_request(group, state.active_revision, request_id),
            body.get("_actor"),
            now,
            expanded_limit=MAX_ENTRIES,
        )
        revisions[key] = prepared["content_revision"]
        changed += prepared["changed"]
        affected.update(affected_contracts(state, prepared))
        latches = invalidate(state, prepared, latches)
    if affected:
        failures = runtime._evaluate(
            state, config, origin="revision", initial_latches=latches, catalog_affected=affected
        )
        if failures:
            runtime.pending_latches = None
            runtime.pending_refresh.clear()
            raise ValueError("catalog_evaluation_failed")
    saved = current_mapping(state)
    if saved["mapping"] != selected["mapping"]:
        version = saved["version"] + 1
        state.tables["title_sync_mapping"][str(version)] = {
            "version": version,
            "mapping": selected["mapping"],
            "at": now.isoformat(),
        }
    result = {
        "request_id": request_id,
        "revisions": revisions,
        "changed": changed,
        "counts": selected["counts"],
        "publication_seq": state.publication_seq,
        "mapping_version": current_mapping(state)["version"],
    }
    state.tables["catalog_request"][request_id] = {"digest": digest, "result": result}
    await runtime._commit(state, publication=bool(affected))
    return result
